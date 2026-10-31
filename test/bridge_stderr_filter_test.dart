import 'package:flutter_test/flutter_test.dart';
import 'package:newchirp/services/radio_bridge.dart';

void main() {
  test('driver replacement notices are not errors, whatever the quoting', () {
    expect(
      isBenignBridgeStderrLine("Replacing existing driver id `Kenwood_TS-2000'"),
      isTrue,
    );
    expect(
      isBenignBridgeStderrLine("Replacing existing driver id 'Kenwood_TS-2000'"),
      isTrue,
    );
    expect(
      isBenignBridgeStderrLine('  Replacing existing driver id "X"  '),
      isTrue,
    );
  });

  test('real errors still pass through', () {
    expect(isBenignBridgeStderrLine('Traceback (most recent call last):'), isFalse);
    expect(
      isBenignBridgeStderrLine('Channel 513: FM2-03 is an FM broadcast preset'),
      isFalse,
    );
  });
}
