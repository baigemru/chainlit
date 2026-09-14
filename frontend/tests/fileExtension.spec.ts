import { describe, expect, it } from 'vitest';

import { fileExtension } from '../src/lib/fileExtension';

const XLSX_MIME =
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

describe('fileExtension', () => {
  it('takes the extension from the name when it has one', () => {
    expect(fileExtension('Отчёт по товарам.xlsx', XLSX_MIME)).toBe('xlsx');
  });

  it('maps a long office mime instead of printing its subtype', () => {
    // The regression: «Образец приложения (Excel)» has no dot, and the old
    // code put the raw subtype on the badge — rendered as «cedocum».
    expect(fileExtension('Образец приложения (Excel)', XLSX_MIME)).toBe('xlsx');
  });

  it('keeps an honest short subtype', () => {
    expect(fileExtension('справка', 'application/pdf')).toBe('pdf');
    expect(fileExtension('архив', 'application/zip')).toBe('zip');
  });

  it('rejects a name tail that is not an extension', () => {
    // «v1.2 отчёт».split('.').pop() is a sentence fragment, not a type.
    expect(fileExtension('v1.2 отчёт', XLSX_MIME)).toBe('xlsx');
  });

  it('falls back to txt when nothing is usable', () => {
    expect(fileExtension('файл')).toBe('txt');
    expect(fileExtension('файл', 'application/x-nonsense.long-subtype')).toBe(
      'txt'
    );
  });
});
