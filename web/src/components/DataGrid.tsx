import type { Preview } from "../types";
export function DataGrid({ preview }: { preview: Preview }) {
  return (
    <>
      <div className="data-scroll">
        <table className="data-grid">
          <thead>
            <tr>
              <th className="row-index">#</th>
              {preview.columns.map((c, i) => (
                <th key={i}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.rows.map((row, i) => (
              <tr key={i}>
                <td className="row-index">{i + 1}</td>
                {row.map((v, j) => (
                  <td key={j} title={v == null ? "NULL" : String(v)}>
                    {v == null ? (
                      <span className="null-value">null</span>
                    ) : (
                      String(v)
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {!preview.rows.length && (
          <div className="small-empty">No qualifying rows.</div>
        )}
      </div>
      <div className="grid-footer">
        <span>
          {preview.result_digest?.row_count?.toLocaleString() ??
            preview.rows.length}{" "}
          result rows
        </span>
        <span>
          {preview.truncated
            ? `Preview · first ${preview.rows.length}`
            : "Complete result"}
        </span>
      </div>
    </>
  );
}
