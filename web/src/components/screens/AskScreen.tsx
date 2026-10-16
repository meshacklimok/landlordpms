import { djangoGet } from "@/lib/django";
import type { AskState } from "@/lib/types";
import { AskPanel } from "./AskPanel";

/** The assistant: a conversation kept in the session, answers from the member's own view of the data (D-091). */
export async function AskScreen() {
  const state = await djangoGet<AskState>("/ask/web-api/");
  return (
    <div className="ask">
      <div className="page-head">
        <div>
          <h1>Ask</h1>
          <p className="lead-text">
            Ask about rent, arrears, vacant units, leases and repairs. Answers come only from what your role can see.
          </p>
        </div>
      </div>
      <AskPanel initial={state} />
    </div>
  );
}
