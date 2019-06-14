/*
 *  Copyright 2017 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree. An additional intellectual property rights grant can be found
 *  in the file PATENTS.  All contributing project authors may
 *  be found in the AUTHORS file in the root of the source tree.
 */

#include "webrtc/sdk/android/src/jni/videoencoderwrapper.h"

#include <utility>

#include "webrtc/common_video/h264/h264_common.h"
#include "webrtc/modules/include/module_common_types.h"
#include "webrtc/modules/video_coding/include/video_codec_interface.h"
#include "webrtc/modules/video_coding/include/video_error_codes.h"
#include "webrtc/modules/video_coding/utility/vp8_header_parser.h"
#include "webrtc/modules/video_coding/utility/vp9_uncompressed_header_parser.h"
#include "webrtc/rtc_base/logging.h"
#include "webrtc/rtc_base/random.h"
#include "webrtc/rtc_base/timeutils.h"
#include "webrtc/sdk/android/src/jni/classreferenceholder.h"

namespace webrtc {
namespace jni {

static const int kMaxJavaEncoderResets = 3;

VideoEncoderWrapper::VideoEncoderWrapper(JNIEnv* jni, jobject j_encoder)
    : encoder_(jni, j_encoder),
      settings_class_(jni, FindClass(jni, "org/webrtc/VideoEncoder$Settings")),
      encode_info_class_(jni,
                         FindClass(jni, "org/webrtc/VideoEncoder$EncodeInfo")),
      frame_type_class_(jni,
                        FindClass(jni, "org/webrtc/EncodedImage$FrameType")),
      bitrate_allocation_class_(
          jni,
          FindClass(jni, "org/webrtc/VideoEncoder$BitrateAllocation")),
      int_array_class_(jni, jni->FindClass("[I")),
      video_frame_factory_(jni) {
  jclass encoder_class = FindClass(jni, "org/webrtc/VideoEncoder");

  init_encode_method_ =
      jni->GetMethodID(encoder_class, "initEncode",
                       "(Lorg/webrtc/VideoEncoder$Settings;Lorg/webrtc/"
                       "VideoEncoder$Callback;)Lorg/webrtc/VideoCodecStatus;");
  release_method_ = jni->GetMethodID(encoder_class, "release",
                                     "()Lorg/webrtc/VideoCodecStatus;");
  encode_method_ = jni->GetMethodID(
      encoder_class, "encode",
      "(Lorg/webrtc/VideoFrame;Lorg/webrtc/"
      "VideoEncoder$EncodeInfo;)Lorg/webrtc/VideoCodecStatus;");
  set_channel_parameters_method_ =
      jni->GetMethodID(encoder_class, "setChannelParameters",
                       "(SJ)Lorg/webrtc/VideoCodecStatus;");
  set_rate_allocation_method_ =
      jni->GetMethodID(encoder_class, "setRateAllocation",
                       "(Lorg/webrtc/VideoEncoder$BitrateAllocation;I)Lorg/"
                       "webrtc/VideoCodecStatus;");
  get_scaling_settings_method_ =
      jni->GetMethodID(encoder_class, "getScalingSettings",
                       "()Lorg/webrtc/VideoEncoder$ScalingSettings;");
  get_implementation_name_method_ = jni->GetMethodID(
      encoder_class, "getImplementationName", "()Ljava/lang/String;");

  settings_constructor_ =
      jni->GetMethodID(*settings_class_, "<init>", "(IIIIIZ)V");

  encode_info_constructor_ = jni->GetMethodID(
      *encode_info_class_, "<init>", "([Lorg/webrtc/EncodedImage$FrameType;)V");

  frame_type_from_native_method_ =
      jni->GetStaticMethodID(*frame_type_class_, "fromNative",
                             "(I)Lorg/webrtc/EncodedImage$FrameType;");

  bitrate_allocation_constructor_ =
      jni->GetMethodID(*bitrate_allocation_class_, "<init>", "([[I)V");

  jclass video_codec_status_class =
      FindClass(jni, "org/webrtc/VideoCodecStatus");
  get_number_method_ =
      jni->GetMethodID(video_codec_status_class, "getNumber", "()I");

  jclass integer_class = jni->FindClass("java/lang/Integer");
  int_value_method_ = jni->GetMethodID(integer_class, "intValue", "()I");

  jclass scaling_settings_class =
      FindClass(jni, "org/webrtc/VideoEncoder$ScalingSettings");
  scaling_settings_on_field_ =
      jni->GetFieldID(scaling_settings_class, "on", "Z");
  scaling_settings_low_field_ =
      jni->GetFieldID(scaling_settings_class, "low", "Ljava/lang/Integer;");
  scaling_settings_high_field_ =
      jni->GetFieldID(scaling_settings_class, "high", "Ljava/lang/Integer;");

  implementation_name_ = GetImplementationName(jni);

  encoder_queue_ = rtc::TaskQueue::Current();

  initialized_ = false;
  num_resets_ = 0;

  Random random(rtc::TimeMicros());
  picture_id_ = random.Rand<uint16_t>() & 0x7FFF;
  tl0_pic_idx_ = random.Rand<uint8_t>();
}

int32_t VideoEncoderWrapper::InitEncode(const VideoCodec* codec_settings,
                                        int32_t number_of_cores,
                                        size_t max_payload_size) {
  JNIEnv* jni = AttachCurrentThreadIfNeeded();
  ScopedLocalRefFrame local_ref_frame(jni);

  number_of_cores_ = number_of_cores;
  codec_settings_ = *codec_settings;
  num_resets_ = 0;

  return InitEncodeInternal(jni);
}

int32_t VideoEncoderWrapper::InitEncodeInternal(JNIEnv* jni) {
  bool automatic_resize_on;
  switch (codec_settings_.codecType) {
    case kVideoCodecVP8:
      automatic_resize_on = codec_settings_.VP8()->automaticResizeOn;
      break;
    case kVideoCodecVP9:
      automatic_resize_on = codec_settings_.VP9()->automaticResizeOn;
      break;
    default:
      automatic_resize_on = true;
  }

  jobject settings =
      jni->NewObject(*settings_class_, settings_constructor_, number_of_cores_,
                     codec_settings_.width, codec_settings_.height,
                     codec_settings_.startBitrate, codec_settings_.maxFramerate,
                     automatic_resize_on);

  jclass callback_class =
      FindClass(jni, "org/webrtc/VideoEncoderWrapperCallback");
  jmethodID callback_constructor =
      jni->GetMethodID(callback_class, "<init>", "(J)V");
  jobject callback = jni->NewObject(callback_class, callback_constructor,
                                    jlongFromPointer(this));

  jobject ret =
      jni->CallObjectMethod(*encoder_, init_encode_method_, settings, callback);
  if (jni->CallIntMethod(ret, get_number_method_) == WEBRTC_VIDEO_CODEC_OK) {
    initialized_ = true;
  }

  return HandleReturnCode(jni, ret);
}

int32_t VideoEncoderWrapper::RegisterEncodeCompleteCallback(
    EncodedImageCallback* callback) {
  callback_ = callback;
  return WEBRTC_VIDEO_CODEC_OK;
}

int32_t VideoEncoderWrapper::Release() {
  JNIEnv* jni = AttachCurrentThreadIfNeeded();
  ScopedLocalRefFrame local_ref_frame(jni);
  jobject ret = jni->CallObjectMethod(*encoder_, release_method_);
  frame_extra_infos_.clear();
  initialized_ = false;
  return HandleReturnCode(jni, ret);
}

int32_t VideoEncoderWrapper::Encode(
    const VideoFrame& frame,
    const CodecSpecificInfo* /* codec_specific_info */,
    const std::vector<FrameType>* frame_types) {
  if (!initialized_) {
    // Most likely initializing the codec failed.
    return WEBRTC_VIDEO_CODEC_FALLBACK_SOFTWARE;
  }

  JNIEnv* jni = AttachCurrentThreadIfNeeded();
  ScopedLocalRefFrame local_ref_frame(jni);

  // Construct encode info.
  jobjectArray j_frame_types =
      jni->NewObjectArray(frame_types->size(), *frame_type_class_, nullptr);
  for (size_t i = 0; i < frame_types->size(); ++i) {
    jobject j_frame_type = jni->CallStaticObjectMethod(
        *frame_type_class_, frame_type_from_native_method_,
        static_cast<jint>((*frame_types)[i]));
    jni->SetObjectArrayElement(j_frame_types, i, j_frame_type);
  }
  jobject encode_info = jni->NewObject(*encode_info_class_,
                                       encode_info_constructor_, j_frame_types);

  FrameExtraInfo info;
  info.capture_time_ns = frame.timestamp_us() * rtc::kNumNanosecsPerMicrosec;
  info.timestamp_rtp = frame.timestamp();
  frame_extra_infos_.push_back(info);

  jobject ret = jni->CallObjectMethod(
      *encoder_, encode_method_, video_frame_factory_.ToJavaFrame(jni, frame),
      encode_info);
  return HandleReturnCode(jni, ret);
}

int32_t VideoEncoderWrapper::SetChannelParameters(uint32_t packet_loss,
                                                  int64_t rtt) {
  JNIEnv* jni = AttachCurrentThreadIfNeeded();
  ScopedLocalRefFrame local_ref_frame(jni);
  jobject ret = jni->CallObjectMethod(*encoder_, set_channel_parameters_method_,
                                      (jshort)packet_loss, (jlong)rtt);
  return HandleReturnCode(jni, ret);
}

int32_t VideoEncoderWrapper::SetRateAllocation(
    const BitrateAllocation& allocation,
    uint32_t framerate) {
  JNIEnv* jni = AttachCurrentThreadIfNeeded();
  ScopedLocalRefFrame local_ref_frame(jni);

  jobject j_bitrate_allocation = ToJavaBitrateAllocation(jni, allocation);
  jobject ret = jni->CallObjectMethod(*encoder_, set_rate_allocation_method_,
                                      j_bitrate_allocation, (jint)framerate);
  return HandleReturnCode(jni, ret);
}

VideoEncoderWrapper::ScalingSettings VideoEncoderWrapper::GetScalingSettings()
    const {
  JNIEnv* jni = AttachCurrentThreadIfNeeded();
  ScopedLocalRefFrame local_ref_frame(jni);
  jobject j_scaling_settings =
      jni->CallObjectMethod(*encoder_, get_scaling_settings_method_);
  bool on =
      jni->GetBooleanField(j_scaling_settings, scaling_settings_on_field_);
  jobject j_low =
      jni->GetObjectField(j_scaling_settings, scaling_settings_low_field_);
  jobject j_high =
      jni->GetObjectField(j_scaling_settings, scaling_settings_high_field_);

  if (j_low != nullptr || j_high != nullptr) {
    RTC_DCHECK(j_low != nullptr);
    RTC_DCHECK(j_high != nullptr);
    int low = jni->CallIntMethod(j_low, int_value_method_);
    int high = jni->CallIntMethod(j_high, int_value_method_);
    return ScalingSettings(on, low, high);
  } else {
    return ScalingSettings(on);
  }
}

const char* VideoEncoderWrapper::ImplementationName() const {
  return implementation_name_.c_str();
}

void VideoEncoderWrapper::OnEncodedFrame(JNIEnv* jni,
                                         jobject j_buffer,
                                         jint encoded_width,
                                         jint encoded_height,
                                         jlong capture_time_ns,
                                         jint frame_type,
                                         jint rotation,
                                         jboolean complete_frame,
                                         jobject j_qp) {
  const uint8_t* buffer =
      static_cast<uint8_t*>(jni->GetDirectBufferAddress(j_buffer));
  const size_t buffer_size = jni->GetDirectBufferCapacity(j_buffer);

  std::vector<uint8_t> buffer_copy(buffer_size);
  memcpy(buffer_copy.data(), buffer, buffer_size);
  int qp = -1;
  if (j_qp != nullptr) {
    qp = jni->CallIntMethod(j_qp, int_value_method_);
  }

  encoder_queue_->PostTask(
      [
        this, task_buffer = std::move(buffer_copy), qp, encoded_width,
        encoded_height, capture_time_ns, frame_type, rotation, complete_frame
      ]() {
        FrameExtraInfo frame_extra_info;
        do {
          if (frame_extra_infos_.empty()) {
            LOG(LS_WARNING)
                << "Java encoder produced an unexpected frame with timestamp: "
                << capture_time_ns;
            return;
          }

          frame_extra_info = frame_extra_infos_.front();
          frame_extra_infos_.pop_front();
          // The encoder might drop frames so iterate through the queue until
          // we find a matching timestamp.
        } while (frame_extra_info.capture_time_ns != capture_time_ns);

        RTPFragmentationHeader header = ParseFragmentationHeader(task_buffer);
        EncodedImage frame(const_cast<uint8_t*>(task_buffer.data()),
                           task_buffer.size(), task_buffer.size());
        frame._encodedWidth = encoded_width;
        frame._encodedHeight = encoded_height;
        frame._timeStamp = frame_extra_info.timestamp_rtp;
        frame.capture_time_ms_ = capture_time_ns / rtc::kNumNanosecsPerMillisec;
        frame._frameType = (FrameType)frame_type;
        frame.rotation_ = (VideoRotation)rotation;
        frame._completeFrame = complete_frame;
        if (qp == -1) {
          frame.qp_ = ParseQp(task_buffer);
        } else {
          frame.qp_ = qp;
        }

        CodecSpecificInfo info(ParseCodecSpecificInfo(frame));
        callback_->OnEncodedImage(frame, &info, &header);
      });
}

int32_t VideoEncoderWrapper::HandleReturnCode(JNIEnv* jni, jobject code) {
  int32_t value = jni->CallIntMethod(code, get_number_method_);
  if (value < 0) {  // Any errors are represented by negative values.
    // Try resetting the codec.
    if (++num_resets_ <= kMaxJavaEncoderResets &&
        Release() == WEBRTC_VIDEO_CODEC_OK) {
      LOG(LS_WARNING) << "Reset Java encoder: " << num_resets_;
      return InitEncodeInternal(jni);
    }

    LOG(LS_WARNING) << "Falling back to software decoder.";
    return WEBRTC_VIDEO_CODEC_FALLBACK_SOFTWARE;
  } else {
    return value;
  }
}

RTPFragmentationHeader VideoEncoderWrapper::ParseFragmentationHeader(
    const std::vector<uint8_t>& buffer) {
  RTPFragmentationHeader header;
  if (codec_settings_.codecType == kVideoCodecH264) {
    h264_bitstream_parser_.ParseBitstream(buffer.data(), buffer.size());

    // For H.264 search for start codes.
    const std::vector<H264::NaluIndex> nalu_idxs =
        H264::FindNaluIndices(buffer.data(), buffer.size());
    if (nalu_idxs.empty()) {
      LOG(LS_ERROR) << "Start code is not found!";
      LOG(LS_ERROR) << "Data:" << buffer[0] << " " << buffer[1] << " "
                    << buffer[2] << " " << buffer[3] << " " << buffer[4] << " "
                    << buffer[5];
    }
    header.VerifyAndAllocateFragmentationHeader(nalu_idxs.size());
    for (size_t i = 0; i < nalu_idxs.size(); i++) {
      header.fragmentationOffset[i] = nalu_idxs[i].payload_start_offset;
      header.fragmentationLength[i] = nalu_idxs[i].payload_size;
      header.fragmentationPlType[i] = 0;
      header.fragmentationTimeDiff[i] = 0;
    }
  } else {
    // Generate a header describing a single fragment.
    header.VerifyAndAllocateFragmentationHeader(1);
    header.fragmentationOffset[0] = 0;
    header.fragmentationLength[0] = buffer.size();
    header.fragmentationPlType[0] = 0;
    header.fragmentationTimeDiff[0] = 0;
  }
  return header;
}

int VideoEncoderWrapper::ParseQp(const std::vector<uint8_t>& buffer) {
  int qp;
  bool success;
  switch (codec_settings_.codecType) {
    case kVideoCodecVP8:
      success = vp8::GetQp(buffer.data(), buffer.size(), &qp);
      break;
    case kVideoCodecVP9:
      success = vp9::GetQp(buffer.data(), buffer.size(), &qp);
      break;
    case kVideoCodecH264:
      success = h264_bitstream_parser_.GetLastSliceQp(&qp);
      break;
    default:  // Default is to not provide QP.
      success = false;
      break;
  }
  return success ? qp : -1;  // -1 means unknown QP.
}

CodecSpecificInfo VideoEncoderWrapper::ParseCodecSpecificInfo(
    const EncodedImage& frame) {
  const bool key_frame = frame._frameType == kVideoFrameKey;

  CodecSpecificInfo info;
  memset(&info, 0, sizeof(info));
  info.codecType = codec_settings_.codecType;
  info.codec_name = implementation_name_.c_str();

  switch (codec_settings_.codecType) {
    case kVideoCodecVP8:
      info.codecSpecific.VP8.pictureId = picture_id_;
      info.codecSpecific.VP8.nonReference = false;
      info.codecSpecific.VP8.simulcastIdx = 0;
      info.codecSpecific.VP8.temporalIdx = kNoTemporalIdx;
      info.codecSpecific.VP8.layerSync = false;
      info.codecSpecific.VP8.tl0PicIdx = kNoTl0PicIdx;
      info.codecSpecific.VP8.keyIdx = kNoKeyIdx;
      break;
    case kVideoCodecVP9:
      if (key_frame) {
        gof_idx_ = 0;
      }
      info.codecSpecific.VP9.picture_id = picture_id_;
      info.codecSpecific.VP9.inter_pic_predicted = key_frame ? false : true;
      info.codecSpecific.VP9.flexible_mode = false;
      info.codecSpecific.VP9.ss_data_available = key_frame ? true : false;
      info.codecSpecific.VP9.tl0_pic_idx = tl0_pic_idx_++;
      info.codecSpecific.VP9.temporal_idx = kNoTemporalIdx;
      info.codecSpecific.VP9.spatial_idx = kNoSpatialIdx;
      info.codecSpecific.VP9.temporal_up_switch = true;
      info.codecSpecific.VP9.inter_layer_predicted = false;
      info.codecSpecific.VP9.gof_idx =
          static_cast<uint8_t>(gof_idx_++ % gof_.num_frames_in_gof);
      info.codecSpecific.VP9.num_spatial_layers = 1;
      info.codecSpecific.VP9.spatial_layer_resolution_present = false;
      if (info.codecSpecific.VP9.ss_data_available) {
        info.codecSpecific.VP9.spatial_layer_resolution_present = true;
        info.codecSpecific.VP9.width[0] = frame._encodedWidth;
        info.codecSpecific.VP9.height[0] = frame._encodedHeight;
        info.codecSpecific.VP9.gof.CopyGofInfoVP9(gof_);
      }
      break;
    default:
      break;
  }

  picture_id_ = (picture_id_ + 1) & 0x7FFF;

  return info;
}

jobject VideoEncoderWrapper::ToJavaBitrateAllocation(
    JNIEnv* jni,
    const BitrateAllocation& allocation) {
  jobjectArray j_allocation_array = jni->NewObjectArray(
      kMaxSpatialLayers, *int_array_class_, nullptr /* initial */);
  for (int spatial_i = 0; spatial_i < kMaxSpatialLayers; ++spatial_i) {
    jintArray j_array_spatial_layer = jni->NewIntArray(kMaxTemporalStreams);
    jint* array_spatial_layer =
        jni->GetIntArrayElements(j_array_spatial_layer, nullptr /* isCopy */);
    for (int temporal_i = 0; temporal_i < kMaxTemporalStreams; ++temporal_i) {
      array_spatial_layer[temporal_i] =
          allocation.GetBitrate(spatial_i, temporal_i);
    }
    jni->ReleaseIntArrayElements(j_array_spatial_layer, array_spatial_layer,
                                 JNI_COMMIT);

    jni->SetObjectArrayElement(j_allocation_array, spatial_i,
                               j_array_spatial_layer);
  }
  return jni->NewObject(*bitrate_allocation_class_,
                        bitrate_allocation_constructor_, j_allocation_array);
}

std::string VideoEncoderWrapper::GetImplementationName(JNIEnv* jni) const {
  jstring jname = reinterpret_cast<jstring>(
      jni->CallObjectMethod(*encoder_, get_implementation_name_method_));
  return JavaToStdString(jni, jname);
}

JNI_FUNCTION_DECLARATION(void,
                         VideoEncoderWrapperCallback_nativeOnEncodedFrame,
                         JNIEnv* jni,
                         jclass,
                         jlong j_native_encoder,
                         jobject buffer,
                         jint encoded_width,
                         jint encoded_height,
                         jlong capture_time_ns,
                         jint frame_type,
                         jint rotation,
                         jboolean complete_frame,
                         jobject qp) {
  VideoEncoderWrapper* native_encoder =
      reinterpret_cast<VideoEncoderWrapper*>(j_native_encoder);
  native_encoder->OnEncodedFrame(jni, buffer, encoded_width, encoded_height,
                                 capture_time_ns, frame_type, rotation,
                                 complete_frame, qp);
}

}  // namespace jni
}  // namespace webrtc
