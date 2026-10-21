# Swahili wording to review

Every Swahili message, next to its English default, filled with example values (tenant Wanjiku Kamau, unit A4, Riverside Court). A native speaker should mark each one OK or give the corrected text. Generated from `notifications/catalog.py`; rerun the generator after edits.

## Invoice issued (`invoice_issued`)

**SMS**

- English: Dear Wanjiku Kamau, invoice INV-2026-000123 for A4 of KES 15,000.00 is due on 05/10/2026. Balance: KES 15,000.00. Pay with reference RIV-A4. https://pms.example.com/p/Ab12 Sunrise Rentals
- Swahili: Mpendwa Wanjiku Kamau, ankara INV-2026-000123 ya A4 ya KES 15,000.00 inalipwa tarehe 05/10/2026. Salio: KES 15,000.00. Lipa kwa kumbukumbu RIV-A4. https://pms.example.com/p/Ab12 Sunrise Rentals
- Template: `Mpendwa {tenant_name}, ankara {invoice_number} ya {unit} ya {amount} inalipwa tarehe {due_date}. Salio: {balance}. Lipa kwa kumbukumbu {pay_reference}. {pay_link} {org_name}`
- Verdict:

**WHATSAPP**

- English: Message from Sunrise Rentals.

Dear Wanjiku Kamau, invoice INV-2026-000123 for A4 of KES 15,000.00 is due on 05/10/2026. Balance: KES 15,000.00. Pay with reference RIV-A4.

Reply STOP to stop WhatsApp messages.
- Swahili: Ujumbe kutoka Sunrise Rentals.

Mpendwa Wanjiku Kamau, ankara INV-2026-000123 ya A4 ya KES 15,000.00 inalipwa tarehe 05/10/2026. Salio: KES 15,000.00. Lipa kwa kumbukumbu RIV-A4.

Jibu STOP kusitisha ujumbe wa WhatsApp.
- Template: `Ujumbe kutoka {org_name}.

Mpendwa {tenant_name}, ankara {invoice_number} ya {unit} ya {amount} inalipwa tarehe {due_date}. Salio: {balance}. Lipa kwa kumbukumbu {pay_reference}.

Jibu STOP kusitisha ujumbe wa WhatsApp.`
- Verdict:

**EMAIL**

- English: Dear Wanjiku Kamau,

Invoice INV-2026-000123 for A4, Riverside Court is attached.

Amount: KES 15,000.00
Due on: 05/10/2026
Balance on your account: KES 15,000.00

Pay with account number RIV-A4.
https://pms.example.com/p/Ab12

Sunrise Rentals
- Swahili: Mpendwa Wanjiku Kamau,

Ankara INV-2026-000123 ya A4, Riverside Court imeambatishwa.

Kiasi: KES 15,000.00
Tarehe ya kulipa: 05/10/2026
Salio la akaunti yako: KES 15,000.00

Lipa kwa nambari ya akaunti RIV-A4.
https://pms.example.com/p/Ab12

Sunrise Rentals
- Template: `Mpendwa {tenant_name},

Ankara {invoice_number} ya {unit}, {property} imeambatishwa.

Kiasi: {amount}
Tarehe ya kulipa: {due_date}
Salio la akaunti yako: {balance}

Lipa kwa nambari ya akaunti {pay_reference}.
{pay_link}

{org_name}`
- Verdict:

## Rent due soon (`rent_due_soon`)

**SMS**

- English: Dear Wanjiku Kamau, a reminder that KES 15,000.00 for A4 is due on 05/10/2026. Pay with reference RIV-A4. https://pms.example.com/p/Ab12 Sunrise Rentals
- Swahili: Mpendwa Wanjiku Kamau, tunakukumbusha kuwa KES 15,000.00 ya A4 inalipwa tarehe 05/10/2026. Lipa kwa kumbukumbu RIV-A4. https://pms.example.com/p/Ab12 Sunrise Rentals
- Template: `Mpendwa {tenant_name}, tunakukumbusha kuwa {amount_due} ya {unit} inalipwa tarehe {due_date}. Lipa kwa kumbukumbu {pay_reference}. {pay_link} {org_name}`
- Verdict:

**WHATSAPP**

- English: Message from Sunrise Rentals.

Dear Wanjiku Kamau, a reminder that KES 15,000.00 for A4 is due on 05/10/2026. Pay with reference RIV-A4.

Reply STOP to stop WhatsApp messages.
- Swahili: Ujumbe kutoka Sunrise Rentals.

Mpendwa Wanjiku Kamau, tunakukumbusha kuwa KES 15,000.00 ya A4 inalipwa tarehe 05/10/2026. Lipa kwa kumbukumbu RIV-A4.

Jibu STOP kusitisha ujumbe wa WhatsApp.
- Template: `Ujumbe kutoka {org_name}.

Mpendwa {tenant_name}, tunakukumbusha kuwa {amount_due} ya {unit} inalipwa tarehe {due_date}. Lipa kwa kumbukumbu {pay_reference}.

Jibu STOP kusitisha ujumbe wa WhatsApp.`
- Verdict:

**EMAIL**

- English: Dear Wanjiku Kamau,

A reminder that KES 15,000.00 for A4, Riverside Court is due on 05/10/2026 (invoice INV-2026-000123).

Pay with account number RIV-A4.
https://pms.example.com/p/Ab12

If you have already paid, thank you, and please ignore this email.

Sunrise Rentals
- Swahili: Mpendwa Wanjiku Kamau,

Tunakukumbusha kuwa KES 15,000.00 ya A4, Riverside Court inalipwa tarehe 05/10/2026 (ankara INV-2026-000123).

Lipa kwa nambari ya akaunti RIV-A4.
https://pms.example.com/p/Ab12

Ikiwa umeshalipa, asante, na tafadhali puuza barua pepe hii.

Sunrise Rentals
- Template: `Mpendwa {tenant_name},

Tunakukumbusha kuwa {amount_due} ya {unit}, {property} inalipwa tarehe {due_date} (ankara {invoice_number}).

Lipa kwa nambari ya akaunti {pay_reference}.
{pay_link}

Ikiwa umeshalipa, asante, na tafadhali puuza barua pepe hii.

{org_name}`
- Verdict:

## Rent overdue (`rent_overdue`)

**SMS**

- English: Dear Wanjiku Kamau, KES 15,000.00 for A4 was due on 05/10/2026 and is still unpaid. Total balance: KES 15,000.00. Pay with reference RIV-A4. https://pms.example.com/p/Ab12 Sunrise Rentals
- Swahili: Mpendwa Wanjiku Kamau, KES 15,000.00 ya A4 ilitakiwa kulipwa tarehe 05/10/2026 na bado haijalipwa. Salio lote: KES 15,000.00. Lipa kwa kumbukumbu RIV-A4. https://pms.example.com/p/Ab12 Sunrise Rentals
- Template: `Mpendwa {tenant_name}, {amount_due} ya {unit} ilitakiwa kulipwa tarehe {due_date} na bado haijalipwa. Salio lote: {balance}. Lipa kwa kumbukumbu {pay_reference}. {pay_link} {org_name}`
- Verdict:

**WHATSAPP**

- English: Message from Sunrise Rentals.

Dear Wanjiku Kamau, KES 15,000.00 for A4 was due on 05/10/2026 and is still unpaid. Total balance: KES 15,000.00. Pay with reference RIV-A4.

Reply STOP to stop WhatsApp messages.
- Swahili: Ujumbe kutoka Sunrise Rentals.

Mpendwa Wanjiku Kamau, KES 15,000.00 ya A4 ilitakiwa kulipwa tarehe 05/10/2026 na bado haijalipwa. Salio lote: KES 15,000.00. Lipa kwa kumbukumbu RIV-A4.

Jibu STOP kusitisha ujumbe wa WhatsApp.
- Template: `Ujumbe kutoka {org_name}.

Mpendwa {tenant_name}, {amount_due} ya {unit} ilitakiwa kulipwa tarehe {due_date} na bado haijalipwa. Salio lote: {balance}. Lipa kwa kumbukumbu {pay_reference}.

Jibu STOP kusitisha ujumbe wa WhatsApp.`
- Verdict:

**EMAIL**

- English: Dear Wanjiku Kamau,

KES 15,000.00 for A4, Riverside Court was due on 05/10/2026 and is still unpaid (invoice INV-2026-000123).

Total balance on your account: KES 15,000.00

Pay with account number RIV-A4.
https://pms.example.com/p/Ab12

If you have already paid, thank you, and please ignore this email.

Sunrise Rentals
- Swahili: Mpendwa Wanjiku Kamau,

KES 15,000.00 ya A4, Riverside Court ilitakiwa kulipwa tarehe 05/10/2026 na bado haijalipwa (ankara INV-2026-000123).

Salio lote la akaunti yako: KES 15,000.00

Lipa kwa nambari ya akaunti RIV-A4.
https://pms.example.com/p/Ab12

Ikiwa umeshalipa, asante, na tafadhali puuza barua pepe hii.

Sunrise Rentals
- Template: `Mpendwa {tenant_name},

{amount_due} ya {unit}, {property} ilitakiwa kulipwa tarehe {due_date} na bado haijalipwa (ankara {invoice_number}).

Salio lote la akaunti yako: {balance}

Lipa kwa nambari ya akaunti {pay_reference}.
{pay_link}

Ikiwa umeshalipa, asante, na tafadhali puuza barua pepe hii.

{org_name}`
- Verdict:

## Payment received (`payment_received`)

**SMS**

- English: Dear Wanjiku Kamau, we received KES 15,000.00 for A4 on 03/10/2026. Receipt RCT-2026-000456. Balance: KES 15,000.00. https://pms.example.com/r/AbCdEf123456 Sunrise Rentals
- Swahili: Mpendwa Wanjiku Kamau, tumepokea KES 15,000.00 ya A4 tarehe 03/10/2026. Risiti RCT-2026-000456. Salio: KES 15,000.00. https://pms.example.com/r/AbCdEf123456 Sunrise Rentals
- Template: `Mpendwa {tenant_name}, tumepokea {amount} ya {unit} tarehe {paid_on}. Risiti {receipt_number}. Salio: {balance}. {receipt_link} {org_name}`
- Verdict:

**WHATSAPP**

- English: Message from Sunrise Rentals.

Dear Wanjiku Kamau, we received KES 15,000.00 for A4 on 03/10/2026. Receipt RCT-2026-000456. Balance: KES 15,000.00. https://pms.example.com/r/AbCdEf123456

Reply STOP to stop WhatsApp messages.
- Swahili: Ujumbe kutoka Sunrise Rentals.

Mpendwa Wanjiku Kamau, tumepokea KES 15,000.00 ya A4 tarehe 03/10/2026. Risiti RCT-2026-000456. Salio: KES 15,000.00. https://pms.example.com/r/AbCdEf123456

Jibu STOP kusitisha ujumbe wa WhatsApp.
- Template: `Ujumbe kutoka {org_name}.

Mpendwa {tenant_name}, tumepokea {amount} ya {unit} tarehe {paid_on}. Risiti {receipt_number}. Salio: {balance}. {receipt_link}

Jibu STOP kusitisha ujumbe wa WhatsApp.`
- Verdict:

**EMAIL**

- English: Dear Wanjiku Kamau,

We received KES 15,000.00 for A4, Riverside Court on 03/10/2026. Thank you.

Receipt RCT-2026-000456 is attached.
Balance on your account: KES 15,000.00

Sunrise Rentals
- Swahili: Mpendwa Wanjiku Kamau,

Tumepokea KES 15,000.00 ya A4, Riverside Court tarehe 03/10/2026. Asante.

Risiti RCT-2026-000456 imeambatishwa.
Salio la akaunti yako: KES 15,000.00

Sunrise Rentals
- Template: `Mpendwa {tenant_name},

Tumepokea {amount} ya {unit}, {property} tarehe {paid_on}. Asante.

Risiti {receipt_number} imeambatishwa.
Salio la akaunti yako: {balance}

{org_name}`
- Verdict:

## Announcement (`announcement`)

**SMS**

- English: Water will be off on Friday from 9am to 1pm. Sunrise Rentals
- Swahili: Water will be off on Friday from 9am to 1pm. Sunrise Rentals
- Template: `{text} {org_name}`
- Verdict:

**WHATSAPP**

- English: Message from Sunrise Rentals.

Water will be off on Friday from 9am to 1pm.

Reply STOP to stop WhatsApp messages.
- Swahili: Ujumbe kutoka Sunrise Rentals.

Water will be off on Friday from 9am to 1pm.

Jibu STOP kusitisha ujumbe wa WhatsApp.
- Template: `Ujumbe kutoka {org_name}.

{text}

Jibu STOP kusitisha ujumbe wa WhatsApp.`
- Verdict:

**EMAIL**

- English: Dear Wanjiku Kamau,

Water will be off on Friday from 9am to 1pm.

Sunrise Rentals
- Swahili: Mpendwa Wanjiku Kamau,

Water will be off on Friday from 9am to 1pm.

Sunrise Rentals
- Template: `Mpendwa {tenant_name},

{text}

{org_name}`
- Verdict:

## Owner statement summary (to the owner) (`owner_statement`)

**SMS**

- English: Dear Mary Achieng, your statement for September 2026: collected KES 120,000.00, management fee , expenses , due to you , paid to you , still to pay . Sunrise Rentals
- Swahili: Mpendwa Mary Achieng, taarifa yako ya September 2026: makusanyo KES 120,000.00, ada ya usimamizi , matumizi , kiasi chako , umelipwa , kilichobaki . Sunrise Rentals
- Template: `Mpendwa {owner_name}, taarifa yako ya {month}: makusanyo {collected}, ada ya usimamizi {fee}, matumizi {expenses}, kiasi chako {due}, umelipwa {remitted}, kilichobaki {remaining}. {org_name}`
- Verdict:

## Maintenance update (to the tenant) (`maintenance_update`)

**SMS**

- English: Dear Wanjiku Kamau, your repair request MR-0042 (Leaking tap) for A4: Done. Plumber fixed the leak. Sunrise Rentals
- Swahili: Mpendwa Wanjiku Kamau, ombi lako la matengenezo MR-0042 (Leaking tap) la A4: Done. Plumber fixed the leak. Sunrise Rentals
- Template: `Mpendwa {tenant_name}, ombi lako la matengenezo {number} ({title}) la {unit}: {status}. {note} {org_name}`
- Verdict:

**WHATSAPP**

- English: Message from Sunrise Rentals.

Dear Wanjiku Kamau, your repair request MR-0042 (Leaking tap) for A4: Done. Plumber fixed the leak.

Reply STOP to stop WhatsApp messages.
- Swahili: Ujumbe kutoka Sunrise Rentals.

Mpendwa Wanjiku Kamau, ombi lako la matengenezo MR-0042 (Leaking tap) la A4: Done. Plumber fixed the leak.

Jibu STOP kusitisha ujumbe wa WhatsApp.
- Template: `Ujumbe kutoka {org_name}.

Mpendwa {tenant_name}, ombi lako la matengenezo {number} ({title}) la {unit}: {status}. {note}

Jibu STOP kusitisha ujumbe wa WhatsApp.`
- Verdict:

**EMAIL**

- English: Dear Wanjiku Kamau,

Your repair request MR-0042 (Leaking tap) for A4, Riverside Court: Done.

Plumber fixed the leak.

Sunrise Rentals
- Swahili: Mpendwa Wanjiku Kamau,

Ombi lako la matengenezo MR-0042 (Leaking tap) la A4, Riverside Court: Done.

Plumber fixed the leak.

Sunrise Rentals
- Template: `Mpendwa {tenant_name},

Ombi lako la matengenezo {number} ({title}) la {unit}, {property}: {status}.

{note}

{org_name}`
- Verdict:

## Maintenance job (to the supplier) (`maintenance_supplier_job`)

**SMS**

- English: Hello Juma Hassan, job MR-0042 at Riverside Court, A4: Leaking tap. Priority: High. Please start by and finish by . Assigned by . Sunrise Rentals
- Swahili: Habari Juma Hassan, kazi MR-0042 katika Riverside Court, A4: Leaking tap. Kipaumbele: High. Tafadhali anza kabla ya na umalize kabla ya . Imetolewa na . Sunrise Rentals
- Template: `Habari {supplier_name}, kazi {number} katika {property}, {unit}: {title}. Kipaumbele: {priority}. Tafadhali anza kabla ya {respond_by} na umalize kabla ya {due}. Imetolewa na {assigned_by}. {org_name}`
- Verdict:

## Notice to vacate received (to the tenant) (`notice_received`)

**SMS**

- English: Dear Wanjiku Kamau, we have your notice to vacate A4. Your move-out day is 30/11/2026. Balance: KES 15,000.00. We will call you to agree the move-out inspection. Sunrise Rentals
- Swahili: Mpendwa Wanjiku Kamau, tumepokea notisi yako ya kuhama A4. Siku yako ya kuhama ni 30/11/2026. Salio: KES 15,000.00. Tutakupigia kupanga ukaguzi wa kuhama. Sunrise Rentals
- Template: `Mpendwa {tenant_name}, tumepokea notisi yako ya kuhama {unit}. Siku yako ya kuhama ni {move_out_on}. Salio: {balance}. Tutakupigia kupanga ukaguzi wa kuhama. {org_name}`
- Verdict:

**WHATSAPP**

- English: Message from Sunrise Rentals.

Dear Wanjiku Kamau, we have your notice to vacate A4. Your move-out day is 30/11/2026. Balance: KES 15,000.00. We will call you to agree the move-out inspection.

Reply STOP to stop WhatsApp messages.
- Swahili: Ujumbe kutoka Sunrise Rentals.

Mpendwa Wanjiku Kamau, tumepokea notisi yako ya kuhama A4. Siku yako ya kuhama ni 30/11/2026. Salio: KES 15,000.00. Tutakupigia kupanga ukaguzi wa kuhama.

Jibu STOP kusitisha ujumbe wa WhatsApp.
- Template: `Ujumbe kutoka {org_name}.

Mpendwa {tenant_name}, tumepokea notisi yako ya kuhama {unit}. Siku yako ya kuhama ni {move_out_on}. Salio: {balance}. Tutakupigia kupanga ukaguzi wa kuhama.

Jibu STOP kusitisha ujumbe wa WhatsApp.`
- Verdict:

**EMAIL**

- English: Dear Wanjiku Kamau,

We have your notice to vacate A4, Riverside Court.

Move-out day: 30/11/2026
Balance on your account: KES 15,000.00

We will call you to agree the move-out inspection and the handing back of keys.

Sunrise Rentals
- Swahili: Mpendwa Wanjiku Kamau,

Tumepokea notisi yako ya kuhama A4, Riverside Court.

Siku ya kuhama: 30/11/2026
Salio la akaunti yako: KES 15,000.00

Tutakupigia kupanga ukaguzi wa kuhama na kurudisha funguo.

Sunrise Rentals
- Template: `Mpendwa {tenant_name},

Tumepokea notisi yako ya kuhama {unit}, {property}.

Siku ya kuhama: {move_out_on}
Salio la akaunti yako: {balance}

Tutakupigia kupanga ukaguzi wa kuhama na kurudisha funguo.

{org_name}`
- Verdict:

## Move-out day coming (to the tenant) (`move_out_soon`)

**SMS**

- English: Dear Wanjiku Kamau, a reminder that you move out of A4 on 30/11/2026. Balance: KES 15,000.00. Please agree a time for the move-out inspection and handing back the keys. Sunrise Rentals
- Swahili: Mpendwa Wanjiku Kamau, tunakukumbusha kuwa unahama A4 tarehe 30/11/2026. Salio: KES 15,000.00. Tafadhali panga muda wa ukaguzi wa kuhama na kurudisha funguo. Sunrise Rentals
- Template: `Mpendwa {tenant_name}, tunakukumbusha kuwa unahama {unit} tarehe {move_out_on}. Salio: {balance}. Tafadhali panga muda wa ukaguzi wa kuhama na kurudisha funguo. {org_name}`
- Verdict:

**WHATSAPP**

- English: Message from Sunrise Rentals.

Dear Wanjiku Kamau, a reminder that you move out of A4 on 30/11/2026. Balance: KES 15,000.00. Please agree a time for the move-out inspection and handing back the keys.

Reply STOP to stop WhatsApp messages.
- Swahili: Ujumbe kutoka Sunrise Rentals.

Mpendwa Wanjiku Kamau, tunakukumbusha kuwa unahama A4 tarehe 30/11/2026. Salio: KES 15,000.00. Tafadhali panga muda wa ukaguzi wa kuhama na kurudisha funguo.

Jibu STOP kusitisha ujumbe wa WhatsApp.
- Template: `Ujumbe kutoka {org_name}.

Mpendwa {tenant_name}, tunakukumbusha kuwa unahama {unit} tarehe {move_out_on}. Salio: {balance}. Tafadhali panga muda wa ukaguzi wa kuhama na kurudisha funguo.

Jibu STOP kusitisha ujumbe wa WhatsApp.`
- Verdict:

**EMAIL**

- English: Dear Wanjiku Kamau,

A reminder that you move out of A4, Riverside Court on 30/11/2026.

Balance on your account: KES 15,000.00

Please agree a time for the move-out inspection and handing back the keys.

Sunrise Rentals
- Swahili: Mpendwa Wanjiku Kamau,

Tunakukumbusha kuwa unahama A4, Riverside Court tarehe 30/11/2026.

Salio la akaunti yako: KES 15,000.00

Tafadhali panga muda wa ukaguzi wa kuhama na kurudisha funguo.

Sunrise Rentals
- Template: `Mpendwa {tenant_name},

Tunakukumbusha kuwa unahama {unit}, {property} tarehe {move_out_on}.

Salio la akaunti yako: {balance}

Tafadhali panga muda wa ukaguzi wa kuhama na kurudisha funguo.

{org_name}`
- Verdict:

## Reply from the office (to the tenant) (`portal_message_reply`)

**SMS**

- English: Dear Wanjiku Kamau, Sunrise Rentals replied to your message. Open your portal to read it.
- Swahili: Mpendwa Wanjiku Kamau, Sunrise Rentals wamejibu ujumbe wako. Fungua tovuti yako kusoma.
- Template: `Mpendwa {tenant_name}, {org_name} wamejibu ujumbe wako. Fungua tovuti yako kusoma.`
- Verdict:

## Renewal offer (to the tenant) (`renewal_offered`)

**SMS**

- English: Dear Wanjiku Kamau, Sunrise Rentals offers to renew your lease of A4 from at . Open your portal to accept or decline by .
- Swahili: Mpendwa Wanjiku Kamau, Sunrise Rentals wanakupa kuongeza muda wa upangaji wa A4 kuanzia kwa . Fungua tovuti yako kukubali au kukataa kabla ya .
- Template: `Mpendwa {tenant_name}, {org_name} wanakupa kuongeza muda wa upangaji wa {unit} kuanzia {start_date} kwa {rent}. Fungua tovuti yako kukubali au kukataa kabla ya {expires_on}.`
- Verdict:
