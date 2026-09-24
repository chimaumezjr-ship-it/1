# KPI Fitness billing (Stripe Billing + Invoicing)

This project handles recurring memberships and one-off invoices for [KPI Fitness](https://www.kpifitness.co.uk/). It runs on Node.js with Express.

| Need | Stripe product | How it's built here |
| --- | --- | --- |
| Monthly/annual memberships | **Billing**: Subscriptions | Hosted Checkout (`mode: 'subscription'`) |
| Members manage cards, change plans, cancel | **Billing**: Customer Portal | `POST /api/portal` |
| Access control, renewals, failed payments | Webhooks | `POST /webhook` → `src/webhooks.js` |
| PT blocks, assessments, corporate sessions | **Invoicing** | `POST /api/admin/invoices` (staff only) |

## Integration plan

1. **Catalog** (`src/catalog.js`, `npm run seed`). Each membership tier is its own Product, and monthly/annual are Prices on that Product. Prices are found by `lookup_key`, so no price IDs are hard-coded. The amounts in the file are placeholders, so set your real pricing before seeding live mode.
2. **Signup.** The app creates one Stripe Customer per member up front, then a Checkout Session in `subscription` mode. The code doesn't pass `payment_method_types`, so Stripe shows every payment method enabled in the Dashboard (cards, Apple/Google Pay, Link, and Bacs Direct Debit if you turn it on). Sessions are tagged with `integration_identifier` for Dashboard reporting.
3. **Access comes from webhooks, not the success page.** The handler covers these events:
   - `checkout.session.completed`/`async_payment_succeeded`. It fulfils only when `payment_status` isn't `unpaid`.
   - `customer.subscription.*`. It re-fetches the subscription so out-of-order delivery can't give the wrong state.
   - `invoice.paid`, `invoice.payment_failed` and `invoice.overdue`.
   - Events are processed idempotently and signatures are always verified.
4. **Self-service.** The Stripe Customer Portal handles plan changes, card updates, invoice history and cancellation.
5. **Invoicing.** Staff raise invoices for catalog services (`{ service: 'pt_session', quantity: 10 }`) or custom lines (`{ description, amount }` in pence). Stripe emails the customer a hosted payment page and a PDF, with `days_until_due` (14 by default).
6. **Revenue recovery.** Stripe Billing retries failed renewals and sends dunning emails, so there's no custom retry logic. Set it up in Dashboard → Billing → Revenue recovery.
7. **Tax (UK VAT).** Prices are created with `tax_behavior: 'inclusive'`. `STRIPE_AUTOMATIC_TAX` stays `false` until you add an active UK VAT registration in Dashboard → Tax → Registrations. If you enable Stripe Tax without a registration, it collects £0 and raises no error. Check with your accountant whether you're over the VAT threshold.

## Getting started

```bash
npm install                      # also sets up the pre-commit hook that blocks committed keys
cp .env.example .env             # fill in keys (see below)

npm i -g @stripe/cli
stripe login                     # or `stripe sandbox create` if you have no account yet
npm run seed                     # create products/prices in your sandbox

stripe listen --forward-to localhost:4242/webhook   # copy the whsec_... into .env
npm start                        # http://localhost:4242
```

Test cards: `4242 4242 4242 4242` succeeds, and `4000 0000 0000 0341` attaches but fails on renewal. You can speed up renewals with test clocks in the Dashboard.

Send a test invoice:

```bash
curl -X POST localhost:4242/api/admin/invoices \
  -H "Authorization: Bearer $ADMIN_API_TOKEN" -H 'Content-Type: application/json' \
  -d '{"email":"client@example.com","name":"Jane Doe","reference":"pt-2026-001",
       "items":[{"service":"pt_session","quantity":10}]}'
```

`npm test` runs the webhook handler tests, including signature verification.

## Security

- Use a **sandbox** for development and CI, not live mode.
- Use **restricted keys** (`rk_...`) with the minimum permissions listed in `.env.example`. The seed script should use a separate key.
- In production, keep keys in your host's secrets vault. Never commit them. `.githooks/pre-commit` blocks `sk_`, `rk_` and `whsec_` strings.
- Turn on 2FA with passkeys or an authenticator app for everyone with Dashboard access.

## Before going live

- [ ] Replace `currentUser()` in `src/server.js` with your real login/session. Right now the demo trusts `userId` from the request body.
- [ ] Replace `JsonStore` with your member database.
- [ ] Set real prices in `src/catalog.js` and run `npm run seed` with a live-mode key.
- [ ] Create a live webhook endpoint for the events in `WEBHOOK_EVENTS` (`src/webhooks.js`) and set `STRIPE_WEBHOOK_SECRET`.
- [ ] Configure the Customer Portal, branding, invoice template, and Revenue recovery (Smart Retries and emails) in the Dashboard.
- [ ] Decide on UK VAT registration, then enable Stripe Tax if it applies.
- [ ] Work through Stripe's [go-live checklist](https://docs.stripe.com/get-started/checklist/go-live).
