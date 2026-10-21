"""Moves the nine articles that were written in code into the table, so they can be edited from the console (D-178)."""

import datetime

from django.db import migrations

ARTICLES = [('collect-rent-on-time',
  'Seven habits that get rent paid on time',
  'Small routines that cut late payment without awkward calls.',
  '## Set one due day\n'
  '\n'
  'Pick a day that follows pay day, such as the 5th, and use it for every unit.\n'
  '\n'
  '## Give one way to pay\n'
  '\n'
  'One Paybill and one account number per unit. Many ways cause mismatches.\n'
  '\n'
  '## Remind before, not after\n'
  '\n'
  'A message two or three days before the due date works better than a notice after it.\n'
  '\n'
  '## Send a receipt every time\n'
  '\n'
  'A receipt ends the argument about whether a payment was made.\n'
  '\n'
  '## Follow up the same week\n'
  '\n'
  'A short message on day one of lateness is easier than a hard one on day thirty.\n'
  '\n'
  '## Write the late fee into the lease\n'
  '\n'
  'A fee the tenant agreed to is easier to apply fairly.\n'
  '\n'
  '## Keep notes of calls\n'
  '\n'
  'Write what was promised and when. It helps if the matter goes further.',
  'RENT'),
 ('paybill-or-till',
  'Paybill or Till for collecting rent',
  'Why a Paybill saves most of the matching work.',
  '## Paybill\n'
  '\n'
  'Tenants type an account number. If it is the unit reference, the payment matches by itself.\n'
  '\n'
  '## Till\n'
  '\n'
  'There is no account number, so each payment must be matched by a person.\n'
  '\n'
  '## Payment prompts\n'
  '\n'
  "Asking a tenant's phone to pay needs a Paybill.\n"
  '\n'
  '## If you have neither\n'
  '\n'
  'Record cash and bank payments by hand and give clear payment instructions.\n'
  '\n'
  '## Tell tenants the reference\n'
  '\n'
  'Print it on the invoice and the receipt so it is never guessed.',
  'RENT'),
 ('fair-water-billing',
  'Billing water fairly by meter',
  'Avoid disputes by reading on a fixed day and showing the numbers.',
  '## Read on the same day\n'
  '\n'
  'A fixed day each month makes the figures comparable.\n'
  '\n'
  '## Photograph the meter\n'
  '\n'
  'A photo settles a disagreement later.\n'
  '\n'
  '## Check unusual readings\n'
  '\n'
  'A very high or negative reading usually means a typing mistake or a leak.\n'
  '\n'
  '## Show the readings on the invoice\n'
  '\n'
  'Tenants accept a bill they can check.\n'
  '\n'
  '## Keep the rate written down\n'
  '\n'
  'Tell tenants the rate per unit and when it changes.',
  'UTILITIES'),
 ('check-rent-against-statement',
  'Check your rent records against the M-Pesa statement',
  'A short monthly routine that catches missed, wrong and duplicate payments.',
  '## Do it on the same day each month\n'
  '\n'
  'After the due-date rush, such as the 10th, so late payers are in.\n'
  '\n'
  '## Download the Paybill statement\n'
  '\n'
  "Take the month from Safaricom's portal and keep a copy.\n"
  '\n'
  '## Compare totals first\n'
  '\n'
  'The total received should equal the total of your recorded payments. If it does, you can stop.\n'
  '\n'
  '## If the totals differ, look for the code\n'
  '\n'
  'Every M-Pesa payment has a unique code. Find the ones that are on one side only.\n'
  '\n'
  '## Clear the inbox\n'
  '\n'
  'Payments sent to a wrong account number wait for you. Match or ignore each one.\n'
  '\n'
  '## Write down what you changed\n'
  '\n'
  'Reverse a wrong payment with a reason rather than deleting it.',
  'RENT'),
 ('landlord-abroad',
  'Looking after a rental in Kenya from abroad',
  'What to set up so the building runs without you being there.',
  '## Money straight to you\n'
  '\n'
  'Use your own Paybill or Till, so rent never passes through a caretaker.\n'
  '\n'
  '## Give your caretaker a role\n'
  '\n'
  'Readings and repair updates, not payments approval or settings.\n'
  '\n'
  '## Let reminders run themselves\n'
  '\n'
  'Invoices and reminders by SMS mean you need not chase anyone.\n'
  '\n'
  '## Look at three figures weekly\n'
  '\n'
  'Collected so far this month, who owes, and empty units.\n'
  '\n'
  '## Ask for photos\n'
  '\n'
  'Meter photos and repair photos prove the work was done.\n'
  '\n'
  '## Agree who decides\n'
  '\n'
  'Decide in advance what the caretaker may spend before asking you.',
  'PEOPLE'),
 ('caretaker-controls',
  'Giving your caretaker access safely',
  'A caretaker needs a few tasks, not your whole business.',
  '## Use a role, not your login\n'
  '\n'
  'Never share your own password.\n'
  '\n'
  '## Limit the properties\n'
  '\n'
  'Show each person only the buildings they look after.\n'
  '\n'
  '## Let money be confirmed by someone else\n'
  '\n'
  'The person who takes cash should not approve it alone.\n'
  '\n'
  '## Turn on two-step login\n'
  '\n'
  'A stolen password is then not enough.\n'
  '\n'
  '## Remove people the day they leave\n'
  '\n'
  'Switch off the seat. The history stays.',
  'PEOPLE'),
 ('move-out-checklist',
  'A move-out checklist for landlords',
  'Steps that make deposit refunds and re-letting smooth.',
  '## Get notice in writing\n'
  '\n'
  'Record the date notice was given and the date the tenant leaves.\n'
  '\n'
  '## Inspect with the tenant\n'
  '\n'
  'Compare with the move-in condition and note anything that differs.\n'
  '\n'
  '## Read final meters\n'
  '\n'
  'Bill the last water and electricity before settling the deposit.\n'
  '\n'
  '## Itemise deductions\n'
  '\n'
  'List each deduction with its reason and amount.\n'
  '\n'
  '## Refund and record it\n'
  '\n'
  'Pay the balance and keep the proof of payment.\n'
  '\n'
  '## Mark the unit vacant\n'
  '\n'
  'An accurate vacancy list tells you what to advertise.',
  'PEOPLE'),
 ('know-your-numbers',
  'Five numbers every landlord should watch',
  'Income, arrears, vacancy, repairs and collection rate.',
  '## Billed against collected\n'
  '\n'
  'The gap between them is your arrears.\n'
  '\n'
  '## Arrears by age\n'
  '\n'
  'Debts more than 60 days old are the hardest to recover.\n'
  '\n'
  '## Vacancy\n'
  '\n'
  'Empty days cost rent. Track how long units sit empty.\n'
  '\n'
  '## Repair spend\n'
  '\n'
  'Compare it to rent per property to spot a building that costs too much.\n'
  '\n'
  '## Paid on time\n'
  '\n'
  'The share of tenants who pay by the due date shows whether your routine works.',
  'RECORDS'),
 ('protect-tenant-data',
  "Looking after your tenants' personal data",
  'What the Data Protection Act asks of a landlord, in plain words.',
  '## Collect only what you need\n'
  '\n'
  'Ask for an ID number only if you have a reason to.\n'
  '\n'
  '## Keep it private\n'
  '\n'
  'Do not share phone numbers or documents in group chats.\n'
  '\n'
  '## Limit who sees it\n'
  '\n'
  'Use roles so staff see only their buildings.\n'
  '\n'
  '## Delete what you no longer need\n'
  '\n'
  'Do not keep records of tenants long after they leave, except what the law requires.\n'
  '\n'
  '## Answer requests\n'
  '\n'
  'A tenant may ask to see or correct their data. Reply in good time.\n'
  '\n'
  '## Take advice\n'
  '\n'
  'This is general information, not legal advice.',
  'RECORDS')]


def load(apps, schema_editor):
    Article = apps.get_model("growth", "Article")
    start = datetime.datetime(2026, 9, 1, 8, 0, tzinfo=datetime.timezone.utc)
    for i, (slug, title, summary, body, category) in enumerate(ARTICLES):
        Article.objects.get_or_create(slug=slug, defaults={
            "title": title, "summary": summary, "body": body, "category": category, "is_published": True,
            "published_at": start + datetime.timedelta(days=i)})


class Migration(migrations.Migration):
    dependencies = [("growth", "0006_article")]
    operations = [migrations.RunPython(load, migrations.RunPython.noop)]
