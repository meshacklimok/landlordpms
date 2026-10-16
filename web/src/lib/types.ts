/** Shapes returned by Django's `/web-api/` views (accounts/web_api.py). Keep the two in step. */

export type NavItem = { label: string; url: string; icon: string; badge: number };
export type NavGroup = { label: string; items: NavItem[] };

export type Notice = { level: string; text: string; url?: string; link?: string };

export type Session = {
  nav: NavGroup[];
  user: { name: string; phone: string; initial: string };
  organization: { name: string; currency: string };
  role: string;
  logout_url: string;
  unread_count: number;
  notices: Notice[];
  email_notice: { has_email: boolean; email: string; url: string } | null;
  terms_notice: boolean;
  has_console: boolean;
  memberships: { id: string; name: string; current: boolean }[];
  urls: Record<
    "help" | "contact" | "security" | "console" | "inbox" | "search" | "suggest" | "switch_org" | "accept_terms" | "terms" | "privacy",
    string
  >;
};

export type Task = {
  key: string;
  count: number;
  label: string;
  url: string;
  detail: string;
  icon: string;
  urgent: boolean;
};

export type QuickAction = { key: string; label: string; url: string; main: boolean };

export type ChecklistStep = { key: string; label: string; url: string; done: boolean; optional: boolean };

export type Figures = {
  month_label: string;
  collected: string;
  expected: string;
  rate: number | null;
  outstanding: string;
  has_outstanding: boolean;
  previous: { label: string; rate: number | null } | null;
  overdue: string;
  overdue_leases: number;
  aging: { key: string; label: string; amount: string; share: number }[];
  arrears_url: string | null;
  occupancy: { rate: number | null; occupied: number; rentable: number; vacant: number };
  trend: { label: string; month: string; rate: number | null; collected: string }[];
};

export type Home = {
  date: string;
  greeting: string;
  user: string;
  organization: string;
  role: string;
  currency: string;
  vocab: { tenant: string; rent: string; units: string };
  dashboard_url: string | null;
  actions: QuickAction[];
  suggest_mfa: boolean;
  security_url: string;
  tasks: Task[];
  reports: { label: string; url: string; date: string }[];
  expiring_count: number;
  expiring: { label: string; tenant: string | null; ends: string; url: string }[];
  started: boolean;
  checklist: { done: number; total: number; steps: ChecklistStep[]; help_url: string } | null;
  figures: Figures | null;
  debtors:
    | { unit: string; who: string; property: string; days: number; overdue: string; phone: string | null; url: string }[]
    | null;
  call_list_url: string | null;
  payments: { label: string; method: string; method_label: string; date: string; amount: string }[] | null;
  payments_url: string | null;
  collection: { label: string; rate: number | null }[];
  summary: {
    occupied: number;
    rentable: number;
    vacant: number;
    reserved: number;
    maintenance: number;
    overdue_leases: number | null;
  } | null;
};

/** `/imports/web-api/switch/` (imports.web_api.SwitchGuideDataView). */
export type SwitchGuide = {
  steps: { title: string; text: string; url: string; action: string; done: boolean }[];
  can_import: boolean;
  can_announce: boolean;
};

/** `/leases/web-api/pickups/<route>/` (leases.web_api.PickupsDataView). */
export type PickupSheet = {
  route: { name: string; id: string };
  day: string;
  today: string;
  month_start: string;
  look_back_days: number;
  rows: { id: number; unit: string; who: string; outcome: "" | "DONE" | "MISSED" | string }[];
  missed: { id: number; unit: string; who: string; missed: number; done: number }[];
  post_url: string;
};

/** `/console/web-api/notifications/` (analytics.web_api). */
export type NotificationType = {
  codename: string;
  label: string;
  audience: string;
  channels: string[];
  offsets: number[];
  enabled: boolean;
  mandatory: boolean;
  urgent: boolean;
  changed: boolean;
};
export type NotificationTypes = { types: NotificationType[] };
export type NotificationTypeDetail = {
  type: NotificationType;
  available_channels: string[];
  placeholders: string[];
};

export type FormField = {
  name: string;
  label: string;
  kind: "text" | "email" | "textarea" | "number" | "checkbox" | "select" | "multicheck" | "image";
  required: boolean;
  help: string;
  value: string | boolean | string[] | null;
  choices?: [string, string][];
  placeholder?: string;
  inputmode?: string;
  accept?: string;
  min?: number;
  max?: number;
  max_length?: number;
};
export type OrganizationSettings = {
  fields: FormField[];
  logo_url: string | null;
  stamp_url: string | null;
  pages: { label: string; hint: string; url: string }[];
  post_url: string;
};

export type AskCard = {
  id: string;
  kind: string;
  title: string;
  lines: [string, string][];
  state: "open" | "done" | "dismissed";
  text: string;
  result: string;
  url: string;
  expired: boolean;
};
export type AskTurn = {
  q: string;
  a: string;
  ok: boolean;
  links: { label: string; url: string }[];
  cards: AskCard[];
};
export type AskState = {
  platform_on: boolean;
  is_on: boolean;
  can_switch: boolean;
  tenants_available: boolean;
  tenants_whatsapp: boolean;
  tenants_on: boolean;
  max_length: number;
  user_daily: number;
  knowledge_count: number;
  knowledge_url: string;
  turns: AskTurn[];
  recent: { question: string; at: string; status: string }[];
  post_url: string;
};
