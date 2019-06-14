# Copyright (c) 2012 The WebRTC project authors. All Rights Reserved.
#
# Use of this source code is governed by a BSD-style license
# that can be found in the LICENSE file in the root of the source
# tree. An additional intellectual property rights grant can be found
# in the file PATENTS.  All contributing project authors may
# be found in the AUTHORS file in the root of the source tree.

import json
import os
import re
import subprocess
import sys


# Files and directories that are *skipped* by cpplint in the presubmit script.
CPPLINT_BLACKLIST = [
  'tools_webrtc',
  'webrtc/api/video_codecs/video_decoder.h',
  'webrtc/examples/objc',
  'webrtc/media',
  'webrtc/modules/audio_coding',
  'webrtc/modules/audio_conference_mixer',
  'webrtc/modules/audio_device',
  'webrtc/modules/audio_processing',
  'webrtc/modules/desktop_capture',
  'webrtc/modules/include/module_common_types.h',
  'webrtc/modules/media_file',
  'webrtc/modules/utility',
  'webrtc/modules/video_capture',
  'webrtc/p2p',
  'webrtc/pc',
  'webrtc/rtc_base',
  'webrtc/sdk/android/src/jni',
  'webrtc/sdk/objc',
  'webrtc/system_wrappers',
  'webrtc/test',
  'webrtc/voice_engine',
  'webrtc/common_types.h',
  'webrtc/common_types.cc',
]

# These filters will always be removed, even if the caller specifies a filter
# set, as they are problematic or broken in some way.
#
# Justifications for each filter:
# - build/c++11         : Rvalue ref checks are unreliable (false positives),
#                         include file and feature blacklists are
#                         google3-specific.
# - whitespace/operators: Same as above (doesn't seem sufficient to eliminate
#                         all move-related errors).
BLACKLIST_LINT_FILTERS = [
  '-build/c++11',
  '-whitespace/operators',
]

# List of directories of "supported" native APIs. That means changes to headers
# will be done in a compatible way following this scheme:
# 1. Non-breaking changes are made.
# 2. The old APIs as marked as deprecated (with comments).
# 3. Deprecation is announced to discuss-webrtc@googlegroups.com and
#    webrtc-users@google.com (internal list).
# 4. (later) The deprecated APIs are removed.
NATIVE_API_DIRS = (
  'webrtc',
  'webrtc/api',
  'webrtc/media',
  'webrtc/modules/audio_device/include',
  'webrtc/pc',
)
# These directories should not be used but are maintained only to avoid breaking
# some legacy downstream code.
LEGACY_API_DIRS = (
  'webrtc/common_audio/include',
  'webrtc/modules/audio_coding/include',
  'webrtc/modules/audio_conference_mixer/include',
  'webrtc/modules/audio_processing/include',
  'webrtc/modules/bitrate_controller/include',
  'webrtc/modules/congestion_controller/include',
  'webrtc/modules/include',
  'webrtc/modules/remote_bitrate_estimator/include',
  'webrtc/modules/rtp_rtcp/include',
  'webrtc/modules/rtp_rtcp/source',
  'webrtc/modules/utility/include',
  'webrtc/modules/video_coding/codecs/h264/include',
  'webrtc/modules/video_coding/codecs/i420/include',
  'webrtc/modules/video_coding/codecs/vp8/include',
  'webrtc/modules/video_coding/codecs/vp9/include',
  'webrtc/modules/video_coding/include',
  'webrtc/rtc_base',
  'webrtc/system_wrappers/include',
  'webrtc/voice_engine/include',
)
API_DIRS = NATIVE_API_DIRS[:] + LEGACY_API_DIRS[:]


def _RunCommand(command, cwd):
  """Runs a command and returns the output from that command."""
  p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       cwd=cwd)
  stdout = p.stdout.read()
  stderr = p.stderr.read()
  p.wait()
  p.stdout.close()
  p.stderr.close()
  return p.returncode, stdout, stderr


def _VerifyNativeApiHeadersListIsValid(input_api, output_api):
  """Ensures the list of native API header directories is up to date."""
  non_existing_paths = []
  native_api_full_paths = [
      input_api.os_path.join(input_api.PresubmitLocalPath(),
                             *path.split('/')) for path in API_DIRS]
  for path in native_api_full_paths:
    if not os.path.isdir(path):
      non_existing_paths.append(path)
  if non_existing_paths:
    return [output_api.PresubmitError(
        'Directories to native API headers have changed which has made the '
        'list in PRESUBMIT.py outdated.\nPlease update it to the current '
        'location of our native APIs.',
        non_existing_paths)]
  return []

API_CHANGE_MSG = """
You seem to be changing native API header files. Please make sure that you:
  1. Make compatible changes that don't break existing clients. Usually
     this is done by keeping the existing method signatures unchanged.
  2. Mark the old stuff as deprecated (see RTC_DEPRECATED macro).
  3. Create a timeline and plan for when the deprecated stuff will be
     removed. (The amount of time we give users to change their code
     should be informed by how much work it is for them. If they just
     need to replace one name with another or something equally
     simple, 1-2 weeks might be good; if they need to do serious work,
     up to 3 months may be called for.)
  4. Update/inform existing downstream code owners to stop using the
     deprecated stuff. (Send announcements to
     discuss-webrtc@googlegroups.com and webrtc-users@google.com.)
  5. Remove the deprecated stuff, once the agreed-upon amount of time
     has passed.
Related files:
"""

def _CheckNativeApiHeaderChanges(input_api, output_api):
  """Checks to remind proper changing of native APIs."""
  files = []
  for f in input_api.AffectedSourceFiles(input_api.FilterSourceFile):
    if f.LocalPath().endswith('.h'):
      for path in API_DIRS:
        if os.path.dirname(f.LocalPath()) == path:
          files.append(f)

  if files:
    return [output_api.PresubmitNotifyResult(API_CHANGE_MSG, files)]
  return []


def _CheckNoIOStreamInHeaders(input_api, output_api):
  """Checks to make sure no .h files include <iostream>."""
  files = []
  pattern = input_api.re.compile(r'^#include\s*<iostream>',
                                 input_api.re.MULTILINE)
  for f in input_api.AffectedSourceFiles(input_api.FilterSourceFile):
    if not f.LocalPath().endswith('.h'):
      continue
    contents = input_api.ReadFile(f)
    if pattern.search(contents):
      files.append(f)

  if len(files):
    return [output_api.PresubmitError(
        'Do not #include <iostream> in header files, since it inserts static ' +
        'initialization into every file including the header. Instead, ' +
        '#include <ostream>. See http://crbug.com/94794',
        files)]
  return []


def _CheckNoPragmaOnce(input_api, output_api):
  """Make sure that banned functions are not used."""
  files = []
  pattern = input_api.re.compile(r'^#pragma\s+once',
                                 input_api.re.MULTILINE)
  for f in input_api.AffectedSourceFiles(input_api.FilterSourceFile):
    if not f.LocalPath().endswith('.h'):
      continue
    contents = input_api.ReadFile(f)
    if pattern.search(contents):
      files.append(f)

  if files:
    return [output_api.PresubmitError(
        'Do not use #pragma once in header files.\n'
        'See http://www.chromium.org/developers/coding-style#TOC-File-headers',
        files)]
  return []


def _CheckNoFRIEND_TEST(input_api, output_api):  # pylint: disable=invalid-name
  """Make sure that gtest's FRIEND_TEST() macro is not used, the
  FRIEND_TEST_ALL_PREFIXES() macro from testsupport/gtest_prod_util.h should be
  used instead since that allows for FLAKY_, FAILS_ and DISABLED_ prefixes."""
  problems = []

  file_filter = lambda f: f.LocalPath().endswith(('.cc', '.h'))
  for f in input_api.AffectedFiles(file_filter=file_filter):
    for line_num, line in f.ChangedContents():
      if 'FRIEND_TEST(' in line:
        problems.append('    %s:%d' % (f.LocalPath(), line_num))

  if not problems:
    return []
  return [output_api.PresubmitPromptWarning('WebRTC\'s code should not use '
      'gtest\'s FRIEND_TEST() macro. Include testsupport/gtest_prod_util.h and '
      'use FRIEND_TEST_ALL_PREFIXES() instead.\n' + '\n'.join(problems))]


def _IsLintBlacklisted(blacklist_paths, file_path):
  """ Checks if a file is blacklisted for lint check."""
  for path in blacklist_paths:
    if file_path == path or os.path.dirname(file_path).startswith(path):
      return True
  return False


def _CheckApprovedFilesLintClean(input_api, output_api,
                                 source_file_filter=None):
  """Checks that all new or non-blacklisted .cc and .h files pass cpplint.py.
  This check is based on _CheckChangeLintsClean in
  depot_tools/presubmit_canned_checks.py but has less filters and only checks
  added files."""
  result = []

  # Initialize cpplint.
  import cpplint
  # Access to a protected member _XX of a client class
  # pylint: disable=W0212
  cpplint._cpplint_state.ResetErrorCounts()

  lint_filters = cpplint._Filters()
  lint_filters.extend(BLACKLIST_LINT_FILTERS)
  cpplint._SetFilters(','.join(lint_filters))

  # Create a platform independent blacklist for cpplint.
  blacklist_paths = [input_api.os_path.join(*path.split('/'))
                     for path in CPPLINT_BLACKLIST]

  # Use the strictest verbosity level for cpplint.py (level 1) which is the
  # default when running cpplint.py from command line. To make it possible to
  # work with not-yet-converted code, we're only applying it to new (or
  # moved/renamed) files and files not listed in CPPLINT_BLACKLIST.
  verbosity_level = 1
  files = []
  for f in input_api.AffectedSourceFiles(source_file_filter):
    # Note that moved/renamed files also count as added.
    if f.Action() == 'A' or not _IsLintBlacklisted(blacklist_paths,
                                                   f.LocalPath()):
      files.append(f.AbsoluteLocalPath())

  for file_name in files:
    cpplint.ProcessFile(file_name, verbosity_level)

  if cpplint._cpplint_state.error_count > 0:
    if input_api.is_committing:
      res_type = output_api.PresubmitError
    else:
      res_type = output_api.PresubmitPromptWarning
    result = [res_type('Changelist failed cpplint.py check.')]

  return result

def _CheckNoSourcesAbove(input_api, gn_files, output_api):
  # Disallow referencing source files with paths above the GN file location.
  source_pattern = input_api.re.compile(r' +sources \+?= \[(.*?)\]',
                                        re.MULTILINE | re.DOTALL)
  file_pattern = input_api.re.compile(r'"((\.\./.*?)|(//.*?))"')
  violating_gn_files = set()
  violating_source_entries = []
  for gn_file in gn_files:
    contents = input_api.ReadFile(gn_file)
    for source_block_match in source_pattern.finditer(contents):
      # Find all source list entries starting with ../ in the source block
      # (exclude overrides entries).
      for file_list_match in file_pattern.finditer(source_block_match.group(1)):
        source_file = file_list_match.group(1)
        if 'overrides/' not in source_file:
          violating_source_entries.append(source_file)
          violating_gn_files.add(gn_file)
  if violating_gn_files:
    return [output_api.PresubmitError(
        'Referencing source files above the directory of the GN file is not '
        'allowed. Please introduce new GN targets in the proper location '
        'instead.\n'
        'Invalid source entries:\n'
        '%s\n'
        'Violating GN files:' % '\n'.join(violating_source_entries),
        items=violating_gn_files)]
  return []

def _CheckNoMixingCAndCCSources(input_api, gn_files, output_api):
  # Disallow mixing .c and .cc source files in the same target.
  source_pattern = input_api.re.compile(r' +sources \+?= \[(.*?)\]',
                                        re.MULTILINE | re.DOTALL)
  file_pattern = input_api.re.compile(r'"(.*)"')
  violating_gn_files = dict()
  for gn_file in gn_files:
    contents = input_api.ReadFile(gn_file)
    for source_block_match in source_pattern.finditer(contents):
      c_files = []
      cc_files = []
      for file_list_match in file_pattern.finditer(source_block_match.group(1)):
        source_file = file_list_match.group(1)
        if source_file.endswith('.c'):
          c_files.append(source_file)
        if source_file.endswith('.cc'):
          cc_files.append(source_file)
      if c_files and cc_files:
        violating_gn_files[gn_file.LocalPath()] = sorted(c_files + cc_files)
  if violating_gn_files:
    return [output_api.PresubmitError(
        'GN targets cannot mix .cc and .c source files. Please create a '
        'separate target for each collection of sources.\n'
        'Mixed sources: \n'
        '%s\n'
        'Violating GN files:' % json.dumps(violating_gn_files, indent=2),
        items=violating_gn_files.keys())]
  return []

def _CheckNoPackageBoundaryViolations(input_api, gn_files, output_api):
  cwd = input_api.PresubmitLocalPath()
  script_path = os.path.join('tools_webrtc', 'presubmit_checks_lib',
                             'check_package_boundaries.py')
  webrtc_path = os.path.join('webrtc')
  command = [sys.executable, script_path, webrtc_path]
  command += [gn_file.LocalPath() for gn_file in gn_files]
  returncode, _, stderr = _RunCommand(command, cwd)
  if returncode:
    return [output_api.PresubmitError(
        'There are package boundary violations in the following GN files:\n\n'
        '%s' % stderr)]
  return []

def _CheckGnChanges(input_api, output_api):
  source_file_filter = lambda x: input_api.FilterSourceFile(
      x, white_list=(r'.+\.(gn|gni)$',))

  gn_files = []
  for f in input_api.AffectedSourceFiles(source_file_filter):
    if f.LocalPath().startswith('webrtc'):
      gn_files.append(f)

  result = []
  if gn_files:
    result.extend(_CheckNoSourcesAbove(input_api, gn_files, output_api))
    result.extend(_CheckNoMixingCAndCCSources(input_api, gn_files, output_api))
    result.extend(_CheckNoPackageBoundaryViolations(
        input_api, gn_files, output_api))
  return result

def _CheckUnwantedDependencies(input_api, output_api):
  """Runs checkdeps on #include statements added in this
  change. Breaking - rules is an error, breaking ! rules is a
  warning.
  """
  # Copied from Chromium's src/PRESUBMIT.py.

  # We need to wait until we have an input_api object and use this
  # roundabout construct to import checkdeps because this file is
  # eval-ed and thus doesn't have __file__.
  original_sys_path = sys.path
  try:
    checkdeps_path = input_api.os_path.join(input_api.PresubmitLocalPath(),
                                            'buildtools', 'checkdeps')
    if not os.path.exists(checkdeps_path):
      return [output_api.PresubmitError(
          'Cannot find checkdeps at %s\nHave you run "gclient sync" to '
          'download Chromium and setup the symlinks?' % checkdeps_path)]
    sys.path.append(checkdeps_path)
    import checkdeps
    from cpp_checker import CppChecker
    from rules import Rule
  finally:
    # Restore sys.path to what it was before.
    sys.path = original_sys_path

  added_includes = []
  for f in input_api.AffectedFiles():
    if not CppChecker.IsCppFile(f.LocalPath()):
      continue

    changed_lines = [line for _, line in f.ChangedContents()]
    added_includes.append([f.LocalPath(), changed_lines])

  deps_checker = checkdeps.DepsChecker(input_api.PresubmitLocalPath())

  error_descriptions = []
  warning_descriptions = []
  for path, rule_type, rule_description in deps_checker.CheckAddedCppIncludes(
      added_includes):
    description_with_path = '%s\n    %s' % (path, rule_description)
    if rule_type == Rule.DISALLOW:
      error_descriptions.append(description_with_path)
    else:
      warning_descriptions.append(description_with_path)

  results = []
  if error_descriptions:
    results.append(output_api.PresubmitError(
        'You added one or more #includes that violate checkdeps rules.\n'
        'Check that the DEPS files in these locations contain valid rules.\n'
        'See https://cs.chromium.org/chromium/src/buildtools/checkdeps/ for '
        'more details about checkdeps.',
        error_descriptions))
  if warning_descriptions:
    results.append(output_api.PresubmitPromptOrNotify(
        'You added one or more #includes of files that are temporarily\n'
        'allowed but being removed. Can you avoid introducing the\n'
        '#include? See relevant DEPS file(s) for details and contacts.\n'
        'See https://cs.chromium.org/chromium/src/buildtools/checkdeps/ for '
        'more details about checkdeps.',
        warning_descriptions))
  return results

def _CheckChangeHasBugField(input_api, output_api):
  """Requires that the changelist have a BUG= field.

  This check is stricter than the one in depot_tools/presubmit_canned_checks.py
  since it fails the presubmit if the BUG= field is missing or doesn't contain
  a bug reference.
  """
  if input_api.change.BUG:
    return []
  else:
    return [output_api.PresubmitError(
        'The BUG=[bug number] field is mandatory. Please create a bug and '
        'reference it using either of:\n'
        ' * https://bugs.webrtc.org - reference it using BUG=webrtc:XXXX\n'
        ' * https://crbug.com - reference it using BUG=chromium:XXXXXX')]

def _CheckJSONParseErrors(input_api, output_api):
  """Check that JSON files do not contain syntax errors."""

  def FilterFile(affected_file):
    return input_api.os_path.splitext(affected_file.LocalPath())[1] == '.json'

  def GetJSONParseError(input_api, filename):
    try:
      contents = input_api.ReadFile(filename)
      input_api.json.loads(contents)
    except ValueError as e:
      return e
    return None

  results = []
  for affected_file in input_api.AffectedFiles(
      file_filter=FilterFile, include_deletes=False):
    parse_error = GetJSONParseError(input_api,
                                    affected_file.AbsoluteLocalPath())
    if parse_error:
      results.append(output_api.PresubmitError('%s could not be parsed: %s' %
          (affected_file.LocalPath(), parse_error)))
  return results


def _RunPythonTests(input_api, output_api):
  def Join(*args):
    return input_api.os_path.join(input_api.PresubmitLocalPath(), *args)

  test_directories = [
      Join('webrtc', 'rtc_tools', 'py_event_log_analyzer'),
      Join('webrtc', 'rtc_tools'),
      Join('webrtc', 'audio', 'test', 'unittests'),
  ] + [
      root for root, _, files in os.walk(Join('tools_webrtc'))
      if any(f.endswith('_test.py') for f in files)
  ]

  tests = []
  for directory in test_directories:
    tests.extend(
      input_api.canned_checks.GetUnitTestsInDirectory(
          input_api,
          output_api,
          directory,
          whitelist=[r'.+_test\.py$']))
  return input_api.RunTests(tests, parallel=True)


def _CheckUsageOfGoogleProtobufNamespace(input_api, output_api):
  """Checks that the namespace google::protobuf has not been used."""
  files = []
  pattern = input_api.re.compile(r'google::protobuf')
  proto_utils_path = os.path.join('webrtc', 'rtc_base', 'protobuf_utils.h')
  for f in input_api.AffectedSourceFiles(input_api.FilterSourceFile):
    if f.LocalPath() in [proto_utils_path, 'PRESUBMIT.py']:
      continue
    contents = input_api.ReadFile(f)
    if pattern.search(contents):
      files.append(f)

  if files:
    return [output_api.PresubmitError(
        'Please avoid to use namespace `google::protobuf` directly.\n'
        'Add a using directive in `%s` and include that header instead.'
        % proto_utils_path, files)]
  return []


def _CommonChecks(input_api, output_api):
  """Checks common to both upload and commit."""
  results = []
  # Filter out files that are in objc or ios dirs from being cpplint-ed since
  # they do not follow C++ lint rules.
  black_list = input_api.DEFAULT_BLACK_LIST + (
    r".*\bobjc[\\\/].*",
    r".*objc\.[hcm]+$",
    r"webrtc\/build\/ios\/SDK\/.*",
  )
  source_file_filter = lambda x: input_api.FilterSourceFile(x, None, black_list)
  results.extend(_CheckApprovedFilesLintClean(
      input_api, output_api, source_file_filter))
  results.extend(input_api.canned_checks.RunPylint(input_api, output_api,
      black_list=(r'^base[\\\/].*\.py$',
                  r'^build[\\\/].*\.py$',
                  r'^buildtools[\\\/].*\.py$',
                  r'^infra[\\\/].*\.py$',
                  r'^ios[\\\/].*\.py$',
                  r'^out.*[\\\/].*\.py$',
                  r'^testing[\\\/].*\.py$',
                  r'^third_party[\\\/].*\.py$',
                  r'^tools[\\\/].*\.py$',
                  # TODO(phoglund): should arguably be checked.
                  r'^tools_webrtc[\\\/]mb[\\\/].*\.py$',
                  r'^tools_webrtc[\\\/]valgrind[\\\/].*\.py$',
                  r'^xcodebuild.*[\\\/].*\.py$',),
      pylintrc='pylintrc'))

  # TODO(nisse): talk/ is no more, so make below checks simpler?
  # WebRTC can't use the presubmit_canned_checks.PanProjectChecks function since
  # we need to have different license checks in talk/ and webrtc/ directories.
  # Instead, hand-picked checks are included below.

  # .m and .mm files are ObjC files. For simplicity we will consider .h files in
  # ObjC subdirectories ObjC headers.
  objc_filter_list = (r'.+\.m$', r'.+\.mm$', r'.+objc\/.+\.h$')
  # Skip long-lines check for DEPS and GN files.
  build_file_filter_list = (r'.+\.gn$', r'.+\.gni$', 'DEPS')
  eighty_char_sources = lambda x: input_api.FilterSourceFile(x,
      black_list=build_file_filter_list + objc_filter_list)
  hundred_char_sources = lambda x: input_api.FilterSourceFile(x,
      white_list=objc_filter_list)
  results.extend(input_api.canned_checks.CheckLongLines(
      input_api, output_api, maxlen=80, source_file_filter=eighty_char_sources))
  results.extend(input_api.canned_checks.CheckLongLines(
      input_api, output_api, maxlen=100,
      source_file_filter=hundred_char_sources))

  results.extend(input_api.canned_checks.CheckChangeHasNoTabs(
      input_api, output_api))
  results.extend(input_api.canned_checks.CheckChangeHasNoStrayWhitespace(
      input_api, output_api))
  results.extend(input_api.canned_checks.CheckAuthorizedAuthor(
      input_api, output_api))
  results.extend(input_api.canned_checks.CheckChangeTodoHasOwner(
      input_api, output_api))
  results.extend(_CheckNativeApiHeaderChanges(input_api, output_api))
  results.extend(_CheckNoIOStreamInHeaders(input_api, output_api))
  results.extend(_CheckNoPragmaOnce(input_api, output_api))
  results.extend(_CheckNoFRIEND_TEST(input_api, output_api))
  results.extend(_CheckGnChanges(input_api, output_api))
  results.extend(_CheckUnwantedDependencies(input_api, output_api))
  results.extend(_CheckJSONParseErrors(input_api, output_api))
  results.extend(_RunPythonTests(input_api, output_api))
  results.extend(_CheckUsageOfGoogleProtobufNamespace(input_api, output_api))
  results.extend(_CheckOrphanHeaders(input_api, output_api))
  results.extend(_CheckNewLineAtTheEndOfProtoFiles(input_api, output_api))
  return results


def CheckChangeOnUpload(input_api, output_api):
  results = []
  results.extend(_CommonChecks(input_api, output_api))
  results.extend(
      input_api.canned_checks.CheckGNFormatted(input_api, output_api))
  return results


def CheckChangeOnCommit(input_api, output_api):
  results = []
  results.extend(_CommonChecks(input_api, output_api))
  results.extend(_VerifyNativeApiHeadersListIsValid(input_api, output_api))
  results.extend(input_api.canned_checks.CheckOwners(input_api, output_api))
  results.extend(input_api.canned_checks.CheckChangeWasUploaded(
      input_api, output_api))
  results.extend(input_api.canned_checks.CheckChangeHasDescription(
      input_api, output_api))
  results.extend(_CheckChangeHasBugField(input_api, output_api))
  results.extend(input_api.canned_checks.CheckTreeIsOpen(
      input_api, output_api,
      json_url='http://webrtc-status.appspot.com/current?format=json'))
  return results


def _CheckOrphanHeaders(input_api, output_api):
  # We need to wait until we have an input_api object and use this
  # roundabout construct to import prebubmit_checks_lib because this file is
  # eval-ed and thus doesn't have __file__.
  error_msg = """Header file {} is not listed in any GN target.
  Please create a target or add it to an existing one in {}"""
  results = []
  original_sys_path = sys.path
  try:
    sys.path = sys.path + [input_api.os_path.join(
        input_api.PresubmitLocalPath(), 'tools_webrtc', 'presubmit_checks_lib')]
    from check_orphan_headers import GetBuildGnPathFromFilePath
    from check_orphan_headers import IsHeaderInBuildGn
  finally:
    # Restore sys.path to what it was before.
    sys.path = original_sys_path

  for f in input_api.AffectedSourceFiles(input_api.FilterSourceFile):
    if f.LocalPath().endswith('.h') and f.Action() == 'A':
      file_path = os.path.abspath(f.LocalPath())
      root_dir = os.getcwd()
      gn_file_path = GetBuildGnPathFromFilePath(file_path, os.path.exists,
                                                root_dir)
      in_build_gn = IsHeaderInBuildGn(file_path, gn_file_path)
      if not in_build_gn:
        results.append(output_api.PresubmitError(error_msg.format(
            file_path, gn_file_path)))
  return results


def _CheckNewLineAtTheEndOfProtoFiles(input_api, output_api):
  """Checks that all .proto files are terminated with a newline."""
  error_msg = 'File {} must end with exactly one newline.'
  results = []
  source_file_filter = lambda x: input_api.FilterSourceFile(
      x, white_list=(r'.+\.proto$',))
  for f in input_api.AffectedSourceFiles(source_file_filter):
    file_path = f.LocalPath()
    with open(file_path) as f:
      lines = f.readlines()
      if lines[-1] != '\n' or lines[-2] == '\n':
        results.append(output_api.PresubmitError(error_msg.format(file_path)))
  return results
