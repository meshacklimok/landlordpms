import { djangoGet } from "@/lib/django";
import type { AnnouncementNew } from "@/lib/types";
import { AnnouncementForm } from "./AnnouncementForm";

/** New announcement (D-214): write a message, choose who gets it, preview, then send. Rules and wording are Django's. */
export async function AnnouncementScreen({ template, days }: { template: string; days: string }) {
  const query = new URLSearchParams();
  if (template) query.set("template", template);
  if (days) query.set("days", days);
  const data = await djangoGet<AnnouncementNew>(`/messages/announcements/web-api/new/?${query}`);
  // A fresh nonce per page load: it is what stops one press of Send going out twice.
  return <AnnouncementForm key={data.nonce} data={data} />;
}
