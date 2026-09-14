/**
 * What to print on a file card's icon.
 *
 * `react-file-icon` renders the `extension` string verbatim on the badge, so
 * whatever comes out of here is user-visible. The old derivation trusted two
 * things it should not have: that everything after the last dot of a name is
 * an extension («v1.2 отчёт» → «2 отчёт»), and that a mime subtype is one —
 * for xlsx that printed a slice of
 * `vnd.openxmlformats-officedocument.spreadsheetml.sheet` («cedocum») on
 * every card whose name had no dot.
 */

// The long-subtype formats a card actually meets. Everything else either has
// an honest subtype (`pdf`, `zip`, `json`, `csv`) or an extension in the name.
const EXTENSION_BY_MIME: Record<string, string> = {
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
  'application/vnd.ms-excel': 'xls',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document':
    'docx',
  'application/msword': 'doc',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation':
    'pptx',
  'application/vnd.ms-powerpoint': 'ppt',
  'application/vnd.oasis.opendocument.spreadsheet': 'ods',
  'application/vnd.oasis.opendocument.text': 'odt',
  'text/plain': 'txt',
  'application/x-tar': 'tar',
  'application/gzip': 'gz'
};

// A plausible extension badge: short and alphanumeric. Anything longer is a
// sentence fragment, not a file type.
const EXTENSION_PATTERN = /^[a-z0-9]{1,7}$/;

const normalized = (candidate: string | undefined): string | undefined => {
  const lowered = candidate?.trim().toLowerCase();
  return lowered && EXTENSION_PATTERN.test(lowered) ? lowered : undefined;
};

export const fileExtension = (name: string, mime?: string): string => {
  const fromName = name.includes('.')
    ? normalized(name.split('.').pop())
    : undefined;
  if (fromName) return fromName;
  if (mime) {
    const mapped = EXTENSION_BY_MIME[mime.toLowerCase()];
    if (mapped) return mapped;
    const subtype = normalized(mime.split('/').pop());
    if (subtype) return subtype;
  }
  return 'txt';
};
