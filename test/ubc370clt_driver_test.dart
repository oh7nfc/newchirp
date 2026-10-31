import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:newchirp/services/radio_bridge.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('catalog exposes the experimental Uniden UBC370CLT driver', () async {
    final source = Directory('build_support/chirp-20261023').existsSync()
        ? 'build_support/chirp-20261023'
        : '../chirp-20260626';
    final bridge = RadioBridge(sourcePath: source, newDriversPath: 'drivers');

    expect(await bridge.models(), contains('Uniden_UBC370CLT'));
    final instructions = await bridge.instructions('Uniden_UBC370CLT');
    expect(instructions.experimental, contains('experimental'));
    expect(instructions.info, contains('115200'));
    expect(instructions.preDownload, contains('program mode'));
  });
}
