import { useRef, useState, type DragEvent, type ChangeEvent, type KeyboardEvent } from 'react';

interface Props {
  onFiles: (files: File[]) => void;
  uploading: boolean;
  /** Path faxes normally arrive by, named so the manual route reads as the exception. */
  inboundPath?: string | null;
}

/**
 * Manual intake, sized like the exception it is.
 *
 * This used to be a 210px drop target sitting above every piece of real data,
 * which framed hand-uploading as the main workflow. It is not: faxes arrive in
 * the watched folder on their own, and the upload path is also the one that
 * needed a duplicate guard bolted on. One line, still a full drop target.
 */
export default function UploadDropzone({ onFiles, uploading, inboundPath }: Props) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const accept = '.pdf,.png,.jpg,.jpeg,.tif,.tiff';

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length) onFiles(files);
  }

  function handleChange(e: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    if (files.length) onFiles(files);
    e.target.value = '';
  }

  function open() {
    if (!uploading) inputRef.current?.click();
  }

  function handleKey(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
  }

  return (
    <div
      className={`intake${dragging ? ' is-dragging' : ''}`}
      role="button"
      tabIndex={0}
      aria-label="Upload a fax by hand"
      onDragOver={e => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      onClick={open}
      onKeyDown={handleKey}
      style={uploading ? { cursor: 'progress' } : undefined}
    >
      <input
        ref={inputRef}
        id="fax-upload-input"
        type="file"
        multiple
        accept={accept}
        style={{ display: 'none' }}
        onChange={handleChange}
        disabled={uploading}
      />

      <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor"
           strokeWidth="1.4" aria-hidden="true">
        <path d="M8 10.5V2.5M8 2.5 5 5.5M8 2.5l3 3M2.5 10v2.5a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1V10" />
      </svg>

      {uploading ? (
        <b>Uploading…</b>
      ) : (
        <>
          <b>
            {inboundPath
              ? 'Faxes arrive on their own from the watched folder.'
              : 'Faxes normally arrive from the inbound folder.'}
          </b>
          <span>
            Need to process one by hand? <span className="cta">Choose a file</span> or drop it here.
          </span>
        </>
      )}
    </div>
  );
}
