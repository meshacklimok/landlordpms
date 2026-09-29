# Entity Relationship Diagram (draft for review)

Derived from doc 11 §2 and §22–27, doc 13 and doc 14. Not code. Every business table also carries `organization_id`, `created_at`, `updated_at`, and archivable tables carry `archived_at/by` (doc 11 §23). Money is `Decimal(14,2)` (doc 11 §25). View with any Mermaid renderer (VS Code Markdown Preview Mermaid extension, GitHub).

## 1. Identity, organizations, roles

```mermaid
erDiagram
    USER ||--o{ MEMBERSHIP : has
    ORGANIZATION ||--o{ MEMBERSHIP : has
    MEMBERSHIP }o--|| ROLE : "assigned"
    MEMBERSHIP ||--o{ MEMBERSHIP_CAPABILITY : "overrides"
    MEMBERSHIP ||--o{ PROPERTY_ACCESS : "scoped to"
    PROPERTY ||--o{ PROPERTY_ACCESS : ""
    ORGANIZATION ||--o{ ROLE : owns
    ROLE_TEMPLATE ||--o{ ROLE : "copied into"
    ROLE ||--o{ ROLE_CAPABILITY : grants
    CAPABILITY ||--o{ ROLE_CAPABILITY : ""
    CAPABILITY ||--o{ MEMBERSHIP_CAPABILITY : ""
    ORGANIZATION ||--o{ INVITATION : sends
    USER ||--o{ AUDIT_EVENT : "acts"
    ORGANIZATION ||--o{ AUDIT_EVENT : ""
```

## 2. Properties, tenants, leases

```mermaid
erDiagram
    ORGANIZATION ||--o{ PROPERTY : owns
    PROPERTY ||--o{ BUILDING : "optional"
    PROPERTY ||--o{ UNIT : contains
    BUILDING ||--o{ UNIT : contains
    ORGANIZATION ||--o{ TENANT : has
    UNIT ||--o{ LEASE : "leased by"
    LEASE ||--o{ LEASE_TENANT : ""
    TENANT ||--o{ LEASE_TENANT : ""
    LEASE ||--o{ LEASE_RENT_CHANGE : "rent history"
    LEASE ||--o{ LEASE_CHARGE : "recurring"
    LEASE ||--o{ LEASE_PAYER : "extra payer phones"
    CHARGE_TYPE ||--o{ LEASE_CHARGE : ""
    PROPERTY }o--o| OWNER_PARTY : "nullable, agency later"
```

## 3. Billing, payments, M-Pesa, deposits (flow B)

```mermaid
erDiagram
    LEASE ||--o{ INVOICE : billed
    INVOICE ||--|{ INVOICE_LINE : has
    CHARGE_TYPE ||--o{ INVOICE_LINE : ""
    LEASE ||--o{ LEDGER_ENTRY : "append-only"
    INVOICE ||--o{ LEDGER_ENTRY : ""
    LEASE ||--o{ DEPOSIT_ENTRY : "isolated sub-ledger"
    TENANT ||--o{ PAYMENT : pays
    LEASE ||--o{ PAYMENT : ""
    PAYMENT ||--o{ PAYMENT_ALLOCATION : splits
    INVOICE ||--o{ PAYMENT_ALLOCATION : receives
    PAYMENT ||--o| RECEIPT : "issues"
    PAYMENT_ACCOUNT ||--o{ PAYMENT : "received on"
    PAYMENT_ACCOUNT }o--o{ PROPERTY : "PropertyPaymentAccount"
    PAYMENT_ACCOUNT ||--o{ MPESA_TRANSACTION : "callbacks"
    MPESA_TRANSACTION ||--o| PAYMENT : "matched to"
    ORGANIZATION ||--o{ NUMBER_SEQUENCE : "locked counters"
```

## 4. Expenses (flow C), platform billing (flow A)

```mermaid
erDiagram
    PROPERTY ||--o{ EXPENSE : "incurred at"
    EXPENSE_CATEGORY ||--o{ EXPENSE : ""
    SUPPLIER ||--o{ EXPENSE : "paid to"
    MAINTENANCE_REQUEST ||--o{ EXPENSE : "optional link"
    PROPERTY ||--o{ MAINTENANCE_REQUEST : ""
    UNIT ||--o{ MAINTENANCE_REQUEST : ""
    PLAN ||--o{ SUBSCRIPTION : ""
    ORGANIZATION ||--o| SUBSCRIPTION : has
    SUBSCRIPTION ||--o{ SUBSCRIPTION_INVOICE : bills
    SUBSCRIPTION_INVOICE ||--o{ SUBSCRIPTION_PAYMENT : ""
    SUBSCRIPTION_PAYMENT ||--o| PLATFORM_RECEIPT : ""
    ORGANIZATION ||--o{ SMS_WALLET_TOPUP : ""
    ORGANIZATION ||--o{ USAGE_COUNTER : ""
```

Flows A, B and C share no tables (doc 11 §22).

## 5. Communications and notification preferences

```mermaid
erDiagram
    ORGANIZATION ||--o{ ORG_NOTIFICATION_RULE : configures
    NOTIFICATION_TYPE ||--o{ ORG_NOTIFICATION_RULE : ""
    NOTIFICATION_TYPE ||--o{ NOTIFICATION_PREFERENCE : ""
    USER ||--o{ NOTIFICATION_PREFERENCE : "staff"
    TENANT ||--o{ NOTIFICATION_PREFERENCE : "tenant"
    USER ||--o{ CONSENT_RECORD : ""
    TENANT ||--o{ CONSENT_RECORD : ""
    MESSAGE_TEMPLATE ||--o{ MESSAGE : ""
    TENANT ||--o{ MESSAGE : receives
    ORGANIZATION ||--o{ MESSAGE : sends
    TENANT ||--o| TENANT_ACCOUNT : "portal login, later"
    USER ||--o| TENANT_ACCOUNT : ""
```

## Points to check in review
1. Is `TENANT_ACCOUNT` the right way to give tenants a portal login without making them staff?
2. `LEDGER_ENTRY` (rent) and `DEPOSIT_ENTRY` (deposits) are separate tables on purpose. Confirm.
3. `MPESA_TRANSACTION` to `PAYMENT` is one-to-one, so an unmatched transaction has no payment yet.
4. One payment may be split across several invoices (`PAYMENT_ALLOCATION`), and one invoice may be paid by several payments.
