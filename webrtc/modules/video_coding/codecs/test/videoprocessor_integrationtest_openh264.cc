/*
 *  Copyright (c) 2017 The WebRTC project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree. An additional intellectual property rights grant can be found
 *  in the file PATENTS.  All contributing project authors may
 *  be found in the AUTHORS file in the root of the source tree.
 */

#include "webrtc/modules/video_coding/codecs/test/videoprocessor_integrationtest.h"

#include <vector>

#include "webrtc/test/testsupport/fileutils.h"

namespace webrtc {
namespace test {

#if defined(WEBRTC_USE_H264)

namespace {

// Codec settings.
const bool kResilienceOn = true;
const int kCifWidth = 352;
const int kCifHeight = 288;
const int kNumFrames = 100;

const std::nullptr_t kNoVisualizationParams = nullptr;

}  // namespace

class VideoProcessorIntegrationTestOpenH264
    : public VideoProcessorIntegrationTest {
 protected:
  VideoProcessorIntegrationTestOpenH264() {
    config_.filename = "foreman_cif";
    config_.input_filename = ResourcePath(config_.filename, "yuv");
    config_.output_filename =
        TempFilename(OutputPath(), "videoprocessor_integrationtest_libvpx");
    config_.networking_config.packet_loss_probability = 0.0;
    // Only allow encoder/decoder to use single core, for predictability.
    config_.use_single_core = true;
    config_.verbose = false;
    config_.hw_encoder = false;
    config_.hw_decoder = false;
  }
};

// H264: Run with no packet loss and fixed bitrate. Quality should be very high.
// Note(hbos): The PacketManipulatorImpl code used to simulate packet loss in
// these unittests appears to drop "packets" in a way that is not compatible
// with H264. Therefore ProcessXPercentPacketLossH264, X != 0, unittests have
// not been added.
TEST_F(VideoProcessorIntegrationTestOpenH264, Process0PercentPacketLossH264) {
  SetCodecSettings(&config_, kVideoCodecH264, 1, false, false, true, false,
                   kResilienceOn, kCifWidth, kCifHeight);

  RateProfile rate_profile;
  SetRateProfile(&rate_profile, 0, 500, 30, 0);
  rate_profile.frame_index_rate_update[1] = kNumFrames + 1;
  rate_profile.num_frames = kNumFrames;

  std::vector<RateControlThresholds> rc_thresholds;
  AddRateControlThresholds(2, 60, 20, 10, 20, 0, 1, &rc_thresholds);

  QualityThresholds quality_thresholds(35.0, 25.0, 0.93, 0.70);

  ProcessFramesAndMaybeVerify(rate_profile, &rc_thresholds, &quality_thresholds,
                              kNoVisualizationParams);
}

#endif  // defined(WEBRTC_USE_H264)

}  // namespace test
}  // namespace webrtc
