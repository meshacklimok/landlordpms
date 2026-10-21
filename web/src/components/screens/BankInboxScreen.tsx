import { djangoGet } from "@/lib/django";
import type { BankInbox } from "@/lib/types";
import { BankInboxList } from "./BankInboxList";

/** Money-in lines from bank statements that could not be placed on a lease. */
export async function BankInboxScreen() {
  const data = await djangoGet<BankInbox>("/bank/web-api/inbox/");
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Bank inbox</h1>
          <p className="lead-text">
            Money-in lines from bank statements that could not be placed on a lease. Matching one confirms it, pays the
            oldest invoices first and issues a receipt.
          </p>
        </div>
        <a className="btn btn-outline" href={data.import_url}>
          <i className="bi bi-upload" aria-hidden="true" />Import a statement
        </a>
      </div>
      <BankInboxList data={data} />
    </>
  );
}
