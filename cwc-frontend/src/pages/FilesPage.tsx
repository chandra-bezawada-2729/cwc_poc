import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  storageApi, formatBytes, relativeTime,
  type StorageBrowse, type StorageFolder, type StorageFile,
} from '../api/client'

/**
 * What is actually on disk, right now.
 *
 * The server has no desktop, so nobody can open a file manager to check where
 * a fax ended up. Without this page, "the fax was filed" is a claim the
 * application makes about itself. This reads the folders directly, so it shows
 * the truth — including files the application does not know about, which is
 * the case worth seeing.
 */
export default function FilesPage() {
  const [data, setData]       = useState<StorageBrowse | null>(null)
  const [error, setError]     = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  async function load() {
    setLoading(true); setError(null)
    try { setData(await storageApi.browse()) }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not read the folders') }
    finally { setLoading(false) }
  }

  useEffect(() => { load() }, [])

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Folders</h1>
          <p className="muted">
            {data ? <>Read from the server just now · base {data.basePath}</> : 'Reading the fax folders on the server'}
          </p>
        </div>
        <button type="button" className="btn" onClick={load} disabled={loading}>
          {loading ? 'Reading…' : 'Refresh'}
        </button>
      </header>

      {error && <div className="alert error">{error}</div>}

      {data?.warnings?.length ? (
        <div className="alert warn">
          {data.warnings.map((w, i) => <div key={i}>{w}</div>)}
        </div>
      ) : null}

      {data?.roots.map(root => <FolderCard key={root.path} folder={root} top />)}

      {data && data.roots.every(r => r.fileCount === 0) && (
        <p className="muted">All three folders are empty.</p>
      )}
    </div>
  )
}

function FolderCard({ folder, top = false }: { folder: StorageFolder; top?: boolean }) {
  // Sub-folders start open: the point of the page is seeing where things went,
  // and a collapsed tree hides exactly that.
  const [open, setOpen] = useState(true)

  return (
    <section className={`card folder-card${top ? ' folder-top' : ''}`}>
      <header className="card-head folder-head" onClick={() => setOpen(o => !o)}>
        <h3>
          <span className="folder-caret" aria-hidden="true">{open ? '▾' : '▸'}</span>
          {folder.name}
        </h3>
        <span className="muted small">
          {folder.fileCount} {folder.fileCount === 1 ? 'file' : 'files'}
          {folder.totalBytes > 0 && <> · {formatBytes(folder.totalBytes)}</>}
        </span>
      </header>

      {top && <p className="muted small folder-path">{folder.path}</p>}

      {open && (
        <>
          {folder.folders.map(sub => <FolderCard key={sub.path} folder={sub} />)}

          {folder.files.length > 0 && (
            <table className="table files-table">
              <thead>
                <tr>
                  <th>File on disk</th>
                  <th>Arrived as</th>
                  <th>Size</th>
                  <th>Modified</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {folder.files.map(f => <FileRow key={folder.path + '/' + f.name} file={f} />)}
              </tbody>
            </table>
          )}

          {folder.truncated && (
            <p className="muted small">
              Showing the most recent 500 files in this folder.
            </p>
          )}

          {folder.files.length === 0 && folder.folders.length === 0 && (
            <p className="muted small">Empty.</p>
          )}
        </>
      )}
    </section>
  )
}

function FileRow({ file }: { file: StorageFile }) {
  return (
    <tr>
      <td className="mono">{file.name}</td>
      <td className="muted">
        {/* The original name matters: it is what the sender called it, and the
            gap between that and the filed name is the AI's contribution. */}
        {file.originalFileName ?? <span className="muted">— not known to the app —</span>}
      </td>
      <td>{formatBytes(file.sizeBytes)}</td>
      <td className="muted">{relativeTime(file.modifiedAt)}</td>
      <td className="row-actions">
        {file.trackingId && (
          <>
            <Link to={`/faxes/${file.trackingId}`} className="link">Details →</Link>
            <a
              href={`/api/faxes/${file.trackingId}/file`}
              target="_blank"
              rel="noreferrer"
              className="link"
              title="Open the PDF"
            >PDF</a>
          </>
        )}
      </td>
    </tr>
  )
}
