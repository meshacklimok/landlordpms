/** Shapes returned by Django's `/web-api/` views (accounts/web_api.py). Keep the two in step. */

export type NavItem = { label: string; url: string; icon: string; badge: number };
export type NavGroup = { label: string; items: NavItem[] };

export type DemoPerson = { key: string; name: string; role: string; current: boolean; url: string };
export type Notice = { level: string; text: string; url?: string; link?: string; people?: DemoPerson[] };

export type Session = {
  nav: NavGroup[];
  user: { name: string; phone: string; initial: string };
  organization: { name: string; currency: string };
  role: string;
  logout_url: string;
  show_ask: boolean;
  in_demo: boolean;
  viewing: { label: string; stop_url: string } | null;
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

export type ChecklistStep = {
  key: string;
  label: string;
  url: string;
  done: boolean;
  optional: boolean;
  hint: string;
  icon: string;
  minutes: number;
};

export type Checklist = {
  done: number;
  total: number;
  next: string | null;
  minutes: number;
  steps: ChecklistStep[];
  help_url: string;
};

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
  checklist: Checklist | null;
  figures: Figures | null;
  debtors:
    | { unit: string; who: string; property: string; days: number; overdue: string; phone: string | null; url: string }[]
    | null;
  call_list_url: string | null;
  payments: { label: string; method: string; method_label: string; date: string; amount: string }[] | null;
  payments_url: string | null;
  collection: { label: string; rate: number | null }[];
  due_soon: { count: number; amount: string; days: number; url: string; remind_url: string | null } | null;
  vacancy: { units: number; amount: string; unpriced: number; url: string | null } | null;
  pace: { now: string; before: string; label: string; change: number | null } | null;
  nudges: { key: "long_empty" | "repeat_late"; count: number; days: number; url: string | null; units: string[] }[];
  net: { cash: string; expenses: string; net: string; negative: boolean; url: string } | null;
  methods: { method: string; label: string; count: number; amount: string; share: number }[];
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
  words: { unit: string; tenant: string };
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
  starters: string[];
  post_url: string;
};

/** `/properties/web-api/hostel/<id>/rooms/` (properties.web_api.RoomMapDataView). */
export type RoomMap = {
  property: { id: string; name: string; url: string };
  free: number;
  taken: number;
  waiting: number;
  booking_url: string;
  can_manage: boolean;
  can_rollover: boolean;
  post_url: string;
  links: { add_rooms: string; waitlist: string; rollover: string };
  rooms: {
    id: string;
    code: string;
    gender: string;
    gender_label: string;
    building: string;
    taken: number;
    beds: number;
    edit_url: string;
    units: { id: string; code: string; occupant: string; free: boolean; reference: string; url: string }[];
  }[];
};

/** One field of a Django form as properties.web_api._describe gives it. */
export type HostelField = {
  name: string;
  label: string;
  kind: "text" | "number" | "date" | "select" | "radio";
  required: boolean;
  help: string;
  value: string;
  choices?: [string, string][];
  min?: number;
  max?: number;
};

/** Add rooms and Edit room (`.../rooms/new/`, `.../room/<id>/`). */
export type HostelFormData = {
  property: { id: string; name: string };
  room?: { code: string };
  fields: HostelField[];
  post_url: string;
  rooms_url: string;
};

/** Next term (`.../hostel/<id>/rollover/`, properties.web_api.RolloverDataView). */
export type Term = {
  property: { id: string; name: string };
  fields: HostelField[];
  students: { id: string; name: string; unit: string; ends: string; drafted: boolean }[];
  drafts: { id: string; name: string; unit: string; starts: string; ends: string }[];
  can_activate: boolean;
  post_url: string;
  rooms_url: string;
};

/** `/properties/web-api/hostel/<id>/waitlist/` (properties.web_api.WaitlistDataView). */
export type Waitlist = {
  property: { id: string; name: string };
  ready: number;
  rooms_url: string;
  rows: {
    name: string;
    phone: string;
    gender: string;
    gender_label: string;
    wants: string;
    since: string;
    offered: string;
    free: number;
    url: string;
  }[];
};

/** `/reports/web-api/hostel/` (reports.web_api.HostelReportDataView). */
export type HostelReport = {
  currency: string;
  ending_within: number;
  show_names: boolean;
  properties: { id: string; name: string }[];
  selected: string;
  csv_url: string;
  rows: {
    property: { id: string; name: string };
    rooms: number;
    beds: number;
    taken: number;
    free: number;
    occupancy: number | null;
    billed: string;
    paid: string;
    owed: string;
    waiting: number;
    ready: number;
    by_gender: { gender: string; beds: number; taken: number; waiting: number }[];
    owing: { student: string; guardian: string; guardian_phone: string; unit: string; owed: string; days_late: number }[];
    ending: { student: string; unit: string; ends: string; drafted: boolean }[];
    rollover_url: string;
  }[];
};

export type AnnouncementStart = {
  text: string; due: boolean; due_within: number; owing: boolean; owing_days: number;
  ending: boolean; ending_within: number; include_co_tenants: boolean; urgent: boolean;
};
export type AnnouncementNew = {
  nonce: string; start: AnnouncementStart; template: string;
  properties: { id: string; name: string }[]; buildings: { id: string; name: string }[];
  can_owing: boolean; can_due: boolean; presets: { key: string; label: string }[]; fields: string[];
  org_name: string; inline_limit: number; post_url: string; list_url: string;
};
export type AnnouncementPreview = {
  sending: number; whatsapp: number; email: number; parts: number; cost: string; price: string; large: boolean;
  skipped: { who: string; unit: string; reason: string }[];
  recipients: { who: string; unit: string; body: string; channel: string; parts: number }[];
  more: number;
};

export type BankImportSummary = {
  id: string; file_name: string; kind: string; kind_label: string; status: string; status_label: string;
  account: string; created_at: string; created_by: string; new: number; matched: number; scanned: boolean; url: string;
};
export type BankImport = {
  accounts: { id: number; name: string; bank: boolean }[]; imports: BankImportSummary[];
  can_add_bank: boolean; accounts_url: string; scan_available: boolean; post_url: string;
};
export type BankImportDetail = BankImportSummary & {
  waiting_for_columns: boolean; map_url: string; expired: boolean; currency: string; duplicates: number;
  skipped: number; unmatched: number; errors: { line: number; message: string }[];
  rows: { line: number | null; date: string; description: string; reference: string; payer: string; amount: string }[];
  more: number; inbox_url: string; import_url: string; post_url: string;
};
export type BankImportMap = {
  id: string; file_name: string; account: string; headers: string[]; samples: string[][];
  fields: { key: string; label: string; help: string; value: string }[]; import_url: string; post_url: string;
};
export type BankInbox = {
  count: number; more: boolean; post_url: string; lines_url: string; import_url: string;
  rows: {
    id: string; description: string; date: string; account: string; reference: string; note: string; amount: string;
    url: string; suggested: { lease: string; unit: string; property: string } | null;
  }[];
};
