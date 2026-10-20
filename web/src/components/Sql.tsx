export function Sql({ sql }: { sql: string }) {
  const tokens = sql.split(
    /('(?:''|[^'])*'|\b(?:SELECT|FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|AS|SUM|COUNT|AVG|MIN|MAX|JOIN|LEFT|INNER|ON|AND|OR|IS|NOT|NULL|TRUE|FALSE|WITH|CASE|WHEN|THEN|ELSE|END|DESC|ASC|CAST|OVER|PARTITION BY)\b)/gi,
  );
  return (
    <pre className="sql-code">
      <code>
        {tokens.map((t, i) => (
          <span
            key={i}
            className={
              t.startsWith("'") ? "sql-string" : i % 2 ? "sql-keyword" : ""
            }
          >
            {t}
          </span>
        ))}
      </code>
    </pre>
  );
}
