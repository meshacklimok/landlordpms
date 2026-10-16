import { djangoGet } from "@/lib/django";
import type { NotificationTypeDetail } from "@/lib/types";
import { NotificationTypeForm } from "./NotificationTypeForm";

export async function NotificationTypeScreen({ codename }: { codename: string }) {
  const detail = await djangoGet<NotificationTypeDetail>(
    `/console/web-api/notifications/${encodeURIComponent(codename)}/`,
  );
  return <NotificationTypeForm detail={detail} />;
}
