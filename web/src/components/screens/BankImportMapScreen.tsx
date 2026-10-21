import { djangoGet } from "@/lib/django";
import type { BankImportMap } from "@/lib/types";
import { BankImportMapForm } from "./BankImportMapForm";

/** We could not tell which column is which: choose them once; they are remembered for the account (D-212). */
export async function BankImportMapScreen({ id }: { id: string }) {
  const data = await djangoGet<BankImportMap>(`/bank/web-api/import/${id}/columns/`);
  return (
    <>
      <div className="page-head">
        <div>
          <p className="crumbs"><a href={data.import_url}>Import a statement</a> / Columns</p>
          <h1>Match the columns</h1>
          <p className="lead-text">
            We could not tell which column is which in {data.file_name}. Choose them once; they are remembered for{" "}
            {data.account}, so next month&rsquo;s statement is read without asking.
          </p>
        </div>
      </div>
      <div className="card table-wrap">
        <table className="data">
          <thead>
            <tr>{data.headers.map((h, i) => <th key={i} scope="col">{h || "—"}</th>)}</tr>
          </thead>
          <tbody>
            {data.samples.map((row, i) => (
              <tr key={i}>{row.map((c, j) => <td key={j}>{c.length > 40 ? `${c.slice(0, 39)}…` : c}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      <BankImportMapForm data={data} />
    </>
  );
}
