import { describe, expect, it } from 'vitest';
import { filterEvidence, isBoilerplate } from './evidence';

/**
 * The "before" list is what Sample 8.pdf (an ophthalmology consult) actually
 * displayed under prompt v2.0, and the "after" list is the clinical content that
 * should survive. These are the cases the filter exists for.
 */
const SAMPLE_8_BOILERPLATE = [
  'Dear Dr.',
  'This is an update on',
  'The following is a summary of my findings',
  'Thank you again for allowing me to assist in the care of',
  'Imp/Plan:',
];

const SAMPLE_8_CLINICAL = [
  'HPI: CC: Dry OU. Severity: mild',
  'Imp/Plan: 1. Diabetes, Type II, No Ocular Complications',
  'Follow Up: Dr. LAI 1 Year - Comprehensive Exam',
  'VA OD: Dsc20/30-2. OS: Dsc20/70',
  'IOP: TP OD: 17 OS: 15',
  'Dry AMD, Early Dry Stage OU',
  'Anterior Blepharitis All 4 Lids',
  'ICD-10 Codes: E11.9, H35.033u, H35.3131u',
];

describe('isBoilerplate', () => {
  it.each(SAMPLE_8_BOILERPLATE)('drops %j', quote => {
    expect(isBoilerplate(quote)).toBe(true);
  });

  it.each(SAMPLE_8_CLINICAL)('keeps %j', quote => {
    expect(isBoilerplate(quote)).toBe(false);
  });

  it('drops a bare section label but keeps the content under it', () => {
    expect(isBoilerplate('FINDINGS:')).toBe(true);
    expect(isBoilerplate('Imp/Plan:')).toBe(true);
    expect(isBoilerplate('FINDINGS: no acute infiltrate')).toBe(false);
  });

  it('drops confidentiality and HIPAA footers wherever the phrase sits', () => {
    expect(isBoilerplate('This fax is confidential and privileged')).toBe(true);
    expect(isBoilerplate('intended only for the named recipient')).toBe(true);
    expect(isBoilerplate('Released per HIPAA authorisation on file')).toBe(true);
  });

  it('drops salutations and closings regardless of case or trailing period', () => {
    expect(isBoilerplate('DEAR DOCTOR')).toBe(true);
    expect(isBoilerplate('Sincerely yours,')).toBe(true);
    expect(isBoilerplate('Thank you for allowing me to participate')).toBe(true);
  });

  it('drops bare cover-sheet field labels', () => {
    for (const label of ['RE:', 'To', 'FROM:', 'Fax:', 'Date', 'Subject:']) {
      expect(isBoilerplate(label)).toBe(true);
    }
  });

  it('treats blank and whitespace-only quotes as boilerplate', () => {
    expect(isBoilerplate('')).toBe(true);
    expect(isBoilerplate('   ')).toBe(true);
  });

  it('does not drop a quote merely for mentioning a doctor by name', () => {
    expect(isBoilerplate('Dear Dr. Lai, referred for diabetic retinopathy screening')).toBe(false);
  });

  it('keeps a measured value that happens to end in a colon-free label', () => {
    expect(isBoilerplate('IOP: TP OD: 17 OS: 15')).toBe(false);
  });
});

describe('filterEvidence', () => {
  it('turns the Sample 8 boilerplate list into an empty group', () => {
    expect(filterEvidence(SAMPLE_8_BOILERPLATE)).toEqual([]);
  });

  it('keeps every clinical quote, in order', () => {
    expect(filterEvidence(SAMPLE_8_CLINICAL)).toEqual(SAMPLE_8_CLINICAL);
  });

  it('separates clinical content from boilerplate in a mixed list', () => {
    const mixed = [
      'Dear Dr.',
      'VA OD: Dsc20/30-2. OS: Dsc20/70',
      'Imp/Plan:',
      'Dry AMD, Early Dry Stage OU',
      'Sincerely',
    ];
    expect(filterEvidence(mixed)).toEqual([
      'VA OD: Dsc20/30-2. OS: Dsc20/70',
      'Dry AMD, Early Dry Stage OU',
    ]);
  });

  it('trims surrounding whitespace on the quotes it keeps', () => {
    expect(filterEvidence(['  IOP: TP OD: 17 OS: 15  '])).toEqual(['IOP: TP OD: 17 OS: 15']);
  });

  it('de-duplicates case-insensitively, keeping the first form seen', () => {
    expect(filterEvidence(['Dry AMD, Early Dry Stage OU', 'dry amd, early dry stage ou']))
      .toEqual(['Dry AMD, Early Dry Stage OU']);
  });

  it('returns an empty array for null, undefined and empty input', () => {
    expect(filterEvidence(null)).toEqual([]);
    expect(filterEvidence(undefined)).toEqual([]);
    expect(filterEvidence([])).toEqual([]);
  });

  it('ignores non-string entries rather than throwing', () => {
    const ragged = ['IOP: TP OD: 17 OS: 15', null, 42, undefined] as unknown as string[];
    expect(filterEvidence(ragged)).toEqual(['IOP: TP OD: 17 OS: 15']);
  });
});
