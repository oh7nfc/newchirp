import 'package:flutter_test/flutter_test.dart';
import 'package:newchirp/models/radio_reference.dart';

void main() {
  group('PMR446', () {
    test('analog FM and DMR Tier 1 use 16 channels 12.5 kHz apart', () {
      final table = pmr446FmDmrTable();
      expect(table.rows, hasLength(16));
      expect(table.rows.first.sublist(0, 2), ['1', '446.006250']);
      expect(table.rows[7].sublist(0, 2), ['8', '446.093750']);
      expect(table.rows.last.sublist(0, 2), ['16', '446.193750']);
    });

    test('dPMR446 uses 32 channels 6.25 kHz apart', () {
      final table = pmr446DpmrTable();
      expect(table.rows, hasLength(32));
      expect(table.rows.first.sublist(0, 2), ['1', '446.003125']);
      expect(table.rows[1].sublist(0, 2), ['2', '446.009375']);
      expect(table.rows.last.sublist(0, 2), ['32', '446.196875']);
    });

    test('each analog channel sits between its two dPMR channels', () {
      for (var channel = 1; channel <= pmr446FmChannels; channel++) {
        final low = pmr446DpmrHertz(channel * 2 - 1);
        final high = pmr446DpmrHertz(channel * 2);
        expect(pmr446FmHertz(channel), (low + high) ~/ 2);
      }
      expect(pmr446DpmrPair(3), '5-6');
      final dpmr = pmr446DpmrTable().rows;
      expect(dpmr[4][2], '3'); // dPMR channel 5 is inside channel 3
      expect(dpmr[5][2], '3');
    });

    test('formatMhz keeps six decimals without rounding errors', () {
      expect(formatMhz(446006250), '446.006250');
      expect(formatMhz(446003125), '446.003125');
      expect(formatMhz(446100000), '446.100000');
    });
  });

  group('tone codes', () {
    test('Motorola PL letter codes', () {
      expect(motorolaPlCodes['67.0'], 'XZ');
      expect(motorolaPlCodes['100.0'], '1Z');
      expect(motorolaPlCodes['254.1'], '0Z');
      expect(motorolaPlCodes, hasLength(42));
      expect(motorolaPlCodes.values.toSet(), hasLength(42));
      expect(motorolaPlCodes['159.8'], isNull);
    });

    test('Talkabout T82 numbers CTCSS 1-38 then DCS 39-121', () {
      expect(talkaboutT82.ctcss, hasLength(38));
      expect(talkaboutT82.dcs, hasLength(83));
      expect(talkaboutT82.ctcssNumber('67.0'), 1);
      expect(talkaboutT82.ctcssNumber('94.8'), 10);
      expect(talkaboutT82.ctcssNumber('250.3'), 38);
      expect(talkaboutT82.ctcssNumber('69.3'), isNull);
      expect(talkaboutT82.dcsNumber('023'), 39);
      expect(talkaboutT82.dcsNumber('754'), 121);
      expect(talkaboutT82.dcsNumber('036'), isNull);
    });

    test('President CB numbers CTCSS and DCS separately', () {
      expect(presidentCb.ctcssNumber('94.8'), 10);
      expect(presidentCb.dcsNumber('023'), 1);
      expect(presidentCb.dcsNumber('073'), 15);
      expect(presidentCb.dcsNumber('754'), 104);
      expect(presidentCb.dcsNumber('006'), isNull);
    });

    test('every code in a set is a standard tone or code', () {
      for (final set in toneCodeSets) {
        expect(standardCtcssTones, containsAll(set.ctcss));
        expect(standardDcsCodes, containsAll(set.dcs));
        expect(set.ctcss.toSet(), hasLength(set.ctcss.length));
        expect(set.dcs.toSet(), hasLength(set.dcs.length));
      }
      expect(standardCtcssTones, hasLength(50));
      expect(standardDcsCodes, hasLength(104));
      expect(standardCtcssTones, containsAll(motorolaPlCodes.keys));
    });

    test('the CTCSS table has one row per tone and a column per radio', () {
      final table = ctcssToneTable();
      expect(table.rows, hasLength(50));
      expect(table.columns, hasLength(2 + toneCodeSets.length));
      final row = table.rows.firstWhere((row) => row[0] == '100.0');
      expect(row, ['100.0', '1Z', '12', '12']);
      final noPl = table.rows.firstWhere((row) => row[0] == '159.8');
      expect(noPl[1], referenceDash);
      final notTalkabout = table.rows.firstWhere((row) => row[0] == '69.3');
      expect(notTalkabout.sublist(2), [referenceDash, referenceDash]);
    });

    test('the DCS table shows each radio number or a dash', () {
      final table = dcsCodeTable();
      expect(table.rows, hasLength(104));
      expect(table.columns, hasLength(1 + toneCodeSets.length));
      expect(table.rows.firstWhere((row) => row[0] == '023'), [
        '023',
        '39',
        '1',
      ]);
      expect(table.rows.firstWhere((row) => row[0] == '036'), [
        '036',
        referenceDash,
        '6',
      ]);
    });

    test('another code set adds a column to both tables', () {
      const other = ToneCodeSet(
        name: 'Test radio',
        ctcss: ['94.8', '100.0'],
        dcs: ['023'],
        dcsNumberedSeparately: true,
      );
      final sets = [talkaboutT82, presidentCb, other];
      final ctcss = ctcssToneTable(sets);
      final dcs = dcsCodeTable(sets);
      expect(ctcss.columns, hasLength(5));
      expect(dcs.columns, hasLength(4));
      expect(ctcss.rows.firstWhere((row) => row[0] == '100.0').last, '2');
      expect(dcs.rows.firstWhere((row) => row[0] == '023').last, '1');
    });
  });

  test('every row has a cell for each column', () {
    for (final section in referenceSections) {
      for (final table in section.tables) {
        for (final row in table.rows) {
          expect(
            row,
            hasLength(table.columns.length),
            reason: '${section.id} / ${table.title}',
          );
        }
      }
    }
  });
}
