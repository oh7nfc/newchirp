/// Reference data for the Frequency reference page: PMR446 channels and the
/// tone code tables of Motorola and President radios.
///
/// The page is data driven. A [ReferenceTable] is a title, some columns and
/// rows of text, and a [ReferenceSection] groups tables under a tab, so another
/// reference is one more entry in [referenceSections]. A radio's tone codes are
/// a [ToneCodeSet]; adding one to [toneCodeSets] adds a column to the tone
/// tables.
library;

/// One column of a reference table.
class ReferenceColumn {
  const ReferenceColumn(this.label, this.width, {this.centered = false});

  final String label;
  final double width;
  final bool centered;
}

/// A titled table of text cells.
class ReferenceTable {
  const ReferenceTable({
    required this.title,
    required this.columns,
    required this.rows,
    this.notes = const [],
  });

  final String title;
  final List<ReferenceColumn> columns;
  final List<List<String>> rows;
  final List<String> notes;
}

/// A tab of the reference page: one or more tables under a heading.
class ReferenceSection {
  const ReferenceSection({
    required this.id,
    required this.label,
    required this.title,
    required this.subtitle,
    required this.tables,
  });

  final String id;

  /// Short name on the tab.
  final String label;
  final String title;
  final String subtitle;
  final List<ReferenceTable> tables;
}

const referenceDash = '—';

/// MHz text of a frequency in hertz, six decimals, without rounding errors.
String formatMhz(int hertz) =>
    '${hertz ~/ 1000000}.${(hertz % 1000000).toString().padLeft(6, '0')}';

// -- PMR446 ------------------------------------------------------------------

const pmr446FmChannels = 16;
const pmr446DpmrChannels = 32;
const _pmr446FmFirstHertz = 446006250;
const _pmr446FmStepHertz = 12500;
const _pmr446DpmrFirstHertz = 446003125;
const _pmr446DpmrStepHertz = 6250;

/// Centre frequency in hertz of analog FM / DMR Tier 1 channel 1-16.
int pmr446FmHertz(int channel) =>
    _pmr446FmFirstHertz + (channel - 1) * _pmr446FmStepHertz;

/// Centre frequency in hertz of dPMR446 channel 1-32.
int pmr446DpmrHertz(int channel) =>
    _pmr446DpmrFirstHertz + (channel - 1) * _pmr446DpmrStepHertz;

/// The two dPMR channels that lie inside analog / DMR channel [channel].
String pmr446DpmrPair(int channel) => '${channel * 2 - 1}-${channel * 2}';

ReferenceTable pmr446FmDmrTable() => ReferenceTable(
  title: 'Analog FM and DMR Tier 1 (12.5 kHz)',
  notes: const [
    'Analog FM and DMR Tier 1 share the same 16 channels, 12.5 kHz apart.',
  ],
  columns: const [
    ReferenceColumn('FM / DMR channel', 120, centered: true),
    ReferenceColumn('Frequency (MHz)', 130),
    ReferenceColumn('dPMR channels inside', 150, centered: true),
  ],
  rows: [
    for (var channel = 1; channel <= pmr446FmChannels; channel++)
      ['$channel', formatMhz(pmr446FmHertz(channel)), pmr446DpmrPair(channel)],
  ],
);

ReferenceTable pmr446DpmrTable() => ReferenceTable(
  title: 'dPMR446 (6.25 kHz)',
  notes: const [
    'dPMR446 uses 32 channels of 6.25 kHz. Each analog / DMR channel covers '
        'two of them.',
  ],
  columns: const [
    ReferenceColumn('dPMR channel', 110, centered: true),
    ReferenceColumn('Frequency (MHz)', 130),
    ReferenceColumn('Inside FM / DMR channel', 170, centered: true),
  ],
  rows: [
    for (var channel = 1; channel <= pmr446DpmrChannels; channel++)
      [
        '$channel',
        formatMhz(pmr446DpmrHertz(channel)),
        '${(channel + 1) ~/ 2}',
      ],
  ],
);

final pmr446Reference = ReferenceSection(
  id: 'pmr446',
  label: 'PMR446',
  title: 'PMR446 Channels',
  subtitle: 'License-free European 446 MHz channels: analog FM, DMR and dPMR',
  tables: [pmr446FmDmrTable(), pmr446DpmrTable()],
);

// -- Tone codes --------------------------------------------------------------

const _standardCtcss =
    '67.0 69.3 71.9 74.4 77.0 79.7 82.5 85.4 88.5 91.5 94.8 97.4 100.0 '
    '103.5 107.2 110.9 114.8 118.8 123.0 127.3 131.8 136.5 141.3 146.2 '
    '151.4 156.7 159.8 162.2 165.5 167.9 171.3 173.8 177.3 179.9 183.5 '
    '186.2 189.9 192.8 196.6 199.5 203.5 206.5 210.7 218.1 225.7 229.1 '
    '233.6 241.8 250.3 254.1';

const _standardDcs =
    '023 025 026 031 032 036 043 047 051 053 054 065 071 072 073 074 '
    '114 115 116 122 125 131 132 134 143 145 152 155 156 162 165 172 '
    '174 205 212 223 225 226 243 244 245 246 251 252 255 261 263 265 '
    '266 271 274 306 311 315 325 331 332 343 346 351 356 364 365 371 '
    '411 412 413 423 431 432 445 446 452 454 455 462 464 465 466 503 '
    '506 516 523 526 532 546 565 606 612 624 627 631 632 654 662 664 '
    '703 712 723 731 732 734 743 754';

const _talkaboutCtcss =
    '67.0 71.9 74.4 77.0 79.7 82.5 85.4 88.5 91.5 94.8 97.4 100.0 '
    '103.5 107.2 110.9 114.8 118.8 123.0 127.3 131.8 136.5 141.3 146.2 '
    '151.4 156.7 162.2 167.9 173.8 179.9 186.2 192.8 203.5 210.7 218.1 '
    '225.7 233.6 241.8 250.3';

const _talkaboutDcs =
    '023 025 026 031 032 043 047 051 054 065 071 072 073 074 114 115 '
    '116 125 131 132 134 143 152 155 156 162 165 172 174 205 223 226 '
    '243 244 245 251 261 263 265 271 306 311 315 331 343 346 351 364 '
    '365 371 411 412 413 423 431 432 445 464 465 466 503 506 516 532 '
    '546 565 606 612 624 627 631 632 654 662 664 703 712 723 731 732 '
    '734 743 754';

const _plFrequencies =
    '67.0 69.3 71.9 74.4 77.0 79.7 82.5 85.4 88.5 91.5 94.8 97.4 100.0 '
    '103.5 107.2 110.9 114.8 118.8 123.0 127.3 131.8 136.5 141.3 146.2 '
    '151.4 156.7 162.2 167.9 173.8 179.9 186.2 192.8 203.5 206.5 210.7 '
    '218.1 225.7 229.1 233.6 241.8 250.3 254.1';

const _plLetterCodes =
    'XZ WZ XA WA XB WB YZ YA YB ZZ ZA ZB 1Z 1A 1B 2Z 2A 2B 3Z 3A 3B 4Z '
    '4A 4B 5Z 5A 5B 6Z 6A 6B 7Z 7A M1 8Z M2 M3 M4 9Z M5 M6 M7 0Z';

/// The CTCSS tones every code set draws from, in Hz as text.
final List<String> standardCtcssTones = _standardCtcss.split(' ');

/// The 104 standard DCS codes (octal, three digits).
final List<String> standardDcsCodes = _standardDcs.split(' ');

/// Motorola PL letter code (for example 1Z for 100.0 Hz) by CTCSS tone.
final Map<String, String> motorolaPlCodes = () {
  final tones = _plFrequencies.split(' ');
  final codes = _plLetterCodes.split(' ');
  assert(tones.length == codes.length);
  return {for (var i = 0; i < tones.length; i++) tones[i]: codes[i]};
}();

/// The tones and codes of one radio family, and how that radio numbers them.
class ToneCodeSet {
  const ToneCodeSet({
    required this.name,
    required this.ctcss,
    required this.dcs,
    this.dcsNumberedSeparately = false,
    this.note,
  });

  final String name;
  final List<String> ctcss;
  final List<String> dcs;

  /// False when the DCS numbers continue after the CTCSS ones (Talkabout:
  /// CTCSS 1-38, DCS 39-121). True when each kind starts again at 1.
  final bool dcsNumberedSeparately;
  final String? note;

  /// The code number the radio shows for a CTCSS tone, or null.
  int? ctcssNumber(String tone) {
    final index = ctcss.indexOf(tone);
    return index < 0 ? null : index + 1;
  }

  /// The code number the radio shows for a DCS code, or null.
  int? dcsNumber(String code) {
    final index = dcs.indexOf(code);
    if (index < 0) return null;
    return index + 1 + (dcsNumberedSeparately ? 0 : ctcss.length);
  }
}

final talkaboutT82 = ToneCodeSet(
  name: 'Motorola Talkabout T82',
  ctcss: _talkaboutCtcss.split(' '),
  dcs: _talkaboutDcs.split(' '),
  note:
      'Talkabout T82 privacy codes 1-121: 1-38 are CTCSS tones, 39-121 are '
      'DCS codes.',
);

final presidentCb = ToneCodeSet(
  name: 'President CB radios',
  ctcss: _talkaboutCtcss.split(' '),
  dcs: standardDcsCodes,
  dcsNumberedSeparately: true,
  note:
      'President CB radios (George II): CTCSS codes 1-38 and DCS codes 1-104 '
      'are numbered separately.',
);

/// Every code set shown in the tone tables, one column each.
final List<ToneCodeSet> toneCodeSets = [talkaboutT82, presidentCb];

String _number(int? number) => number == null ? referenceDash : '$number';

ReferenceTable ctcssToneTable([List<ToneCodeSet>? sets]) {
  final codeSets = sets ?? toneCodeSets;
  return ReferenceTable(
    title: 'CTCSS tones',
    notes: [
      'Motorola PL is the letter code Motorola radios use for a CTCSS tone.',
      for (final set in codeSets)
        if (set.note != null) set.note!,
      '$referenceDash means the radio has no such tone.',
    ],
    columns: [
      const ReferenceColumn('CTCSS (Hz)', 90, centered: true),
      const ReferenceColumn('Motorola PL', 100, centered: true),
      for (final set in codeSets)
        ReferenceColumn(set.name, 150, centered: true),
    ],
    rows: [
      for (final tone in standardCtcssTones)
        [
          tone,
          motorolaPlCodes[tone] ?? referenceDash,
          for (final set in codeSets) _number(set.ctcssNumber(tone)),
        ],
    ],
  );
}

ReferenceTable dcsCodeTable([List<ToneCodeSet>? sets]) {
  final codeSets = sets ?? toneCodeSets;
  return ReferenceTable(
    title: 'DCS codes',
    notes: ['$referenceDash means the radio has no such code.'],
    columns: [
      const ReferenceColumn('DCS code', 90, centered: true),
      for (final set in codeSets)
        ReferenceColumn(set.name, 150, centered: true),
    ],
    rows: [
      for (final code in standardDcsCodes)
        [code, for (final set in codeSets) _number(set.dcsNumber(code))],
    ],
  );
}

final toneCodeReference = ReferenceSection(
  id: 'tones',
  label: 'Tone codes',
  title: 'Tone Code Reference',
  subtitle:
      'CTCSS and DCS codes, Motorola PL codes, Talkabout T82 and President CB',
  tables: [ctcssToneTable(), dcsCodeTable()],
);

/// The tabs after GMRS & FRS on the reference page.
final List<ReferenceSection> referenceSections = [
  pmr446Reference,
  toneCodeReference,
];
