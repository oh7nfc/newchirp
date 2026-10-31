import 'package:flutter_test/flutter_test.dart';
import 'package:newchirp/main.dart';

void main() {
  test('a column of blanks and defaults is unused', () {
    expect(columnIsUnused(['—', '—', '—']), isTrue);
    expect(columnIsUnused(['Simplex', 'Simplex']), isTrue);
    expect(columnIsUnused(['None', '', '—']), isTrue);
  });

  test('one real value keeps the column', () {
    expect(columnIsUnused(['—', 'Cross', '—']), isFalse);
    expect(columnIsUnused(['Simplex', '-']), isFalse);
  });

  test('no channels means nothing to judge, so the column stays', () {
    expect(columnIsUnused(const <String>[]), isFalse);
  });
}
