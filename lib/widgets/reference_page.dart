import 'package:flutter/material.dart';

import '../models/radio_reference.dart';

const _green = Color(0xFF1F7455);
const _ink = Color(0xFF15211D);
const _muted = Color(0xFF56615B);
const _grid = Color(0xFFD8DEDA);
const _stripe = Color(0xFFF5F8F5);

/// Renders one [ReferenceSection]: its heading, a filter box and every table.
/// The filter keeps the rows that contain the typed text in any cell, in all
/// tables at once.
class ReferencePage extends StatefulWidget {
  const ReferencePage({super.key, required this.section});

  final ReferenceSection section;

  @override
  State<ReferencePage> createState() => _ReferencePageState();
}

class _ReferencePageState extends State<ReferencePage> {
  final _filter = TextEditingController();
  String _query = '';

  @override
  void dispose() {
    _filter.dispose();
    super.dispose();
  }

  List<List<String>> _visibleRows(ReferenceTable table) {
    if (_query.isEmpty) return table.rows;
    return table.rows
        .where((row) => row.any((cell) => cell.toLowerCase().contains(_query)))
        .toList();
  }

  @override
  Widget build(BuildContext context) {
    final section = widget.section;
    return SingleChildScrollView(
      key: PageStorageKey('reference-${section.id}'),
      padding: const EdgeInsets.all(24),
      child: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 980),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                section.title,
                style: Theme.of(context).textTheme.headlineSmall?.copyWith(
                  color: _green,
                  fontWeight: FontWeight.w700,
                ),
              ),
              const SizedBox(height: 5),
              Text(
                section.subtitle,
                style: Theme.of(
                  context,
                ).textTheme.bodyMedium?.copyWith(color: _muted),
              ),
              const SizedBox(height: 14),
              Align(
                alignment: Alignment.centerLeft,
                child: SizedBox(
                  width: 320,
                  child: TextField(
                    key: ValueKey('reference-filter-${section.id}'),
                    controller: _filter,
                    onChanged: (value) =>
                        setState(() => _query = value.trim().toLowerCase()),
                    decoration: InputDecoration(
                      isDense: true,
                      prefixIcon: const Icon(Icons.search, size: 20),
                      hintText: 'Filter rows',
                      suffixIcon: _query.isEmpty
                          ? null
                          : IconButton(
                              tooltip: 'Clear filter',
                              icon: const Icon(Icons.close, size: 18),
                              onPressed: () {
                                _filter.clear();
                                setState(() => _query = '');
                              },
                            ),
                    ),
                  ),
                ),
              ),
              for (final table in section.tables) ...[
                const SizedBox(height: 20),
                _ReferenceTableView(table: table, rows: _visibleRows(table)),
              ],
            ],
          ),
        ),
      ),
    );
  }
}

class _ReferenceTableView extends StatelessWidget {
  const _ReferenceTableView({required this.table, required this.rows});

  final ReferenceTable table;
  final List<List<String>> rows;

  Widget _cell(String text, {required bool centered, bool header = false}) =>
      Padding(
        padding: EdgeInsets.symmetric(
          horizontal: 10,
          vertical: header ? 10 : 8,
        ),
        child: Text(
          text,
          textAlign: centered ? TextAlign.center : TextAlign.left,
          style: TextStyle(
            color: header ? Colors.white : _ink,
            fontWeight: header ? FontWeight.w700 : FontWeight.w400,
            fontSize: 11,
          ),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final columns = table.columns;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          table.title,
          style: const TextStyle(
            color: _ink,
            fontWeight: FontWeight.w700,
            fontSize: 15,
          ),
        ),
        for (final note in table.notes)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
              note,
              style: const TextStyle(color: _muted, fontSize: 12),
            ),
          ),
        const SizedBox(height: 8),
        if (rows.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 8),
            child: Text(
              'No matching rows',
              style: TextStyle(color: _muted, fontSize: 12),
            ),
          )
        else
          Card(
            clipBehavior: Clip.antiAlias,
            child: SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Table(
                border: TableBorder.all(color: _grid, width: 0.6),
                columnWidths: {
                  for (var i = 0; i < columns.length; i++)
                    i: FixedColumnWidth(columns[i].width),
                },
                children: [
                  TableRow(
                    decoration: const BoxDecoration(color: _green),
                    children: [
                      for (final column in columns)
                        _cell(
                          column.label,
                          centered: column.centered,
                          header: true,
                        ),
                    ],
                  ),
                  for (var r = 0; r < rows.length; r++)
                    TableRow(
                      decoration: BoxDecoration(
                        color: r.isOdd ? _stripe : Colors.white,
                      ),
                      children: [
                        for (var c = 0; c < columns.length; c++)
                          _cell(rows[r][c], centered: columns[c].centered),
                      ],
                    ),
                ],
              ),
            ),
          ),
      ],
    );
  }
}
