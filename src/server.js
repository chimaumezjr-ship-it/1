import crypto from 'node:crypto';
import path from 'node:path';
import express from 'express';
import { getStripe } from './stripe.js';
import { JsonStore } from './store.js';
import { handleEvent } from './webhooks.js';
import { createAndSendInvoice, createMembershipCheckout, createPortalSession } from './billing.js';

const PORT = process.env.PORT ?? 4242;
const BASE_URL = process.env.BASE_URL ?? `http://localhost:${PORT}`;

const stripe = getStripe();
const store = new JsonStore(process.env.DATA_FILE ?? path.resolve('data/store.json'));
const ctx = { stripe, store, log: console };

const app = express();

// Webhook route must see the raw body for signature verification, so it is
// registered before express.json().
app.post('/webhook', express.raw({ type: 'application/json' }), async (req, res) => {
  let event;
  try {
    event = stripe.webhooks.constructEvent(
      req.body,
      req.headers['stripe-signature'],
      process.env.STRIPE_WEBHOOK_SECRET,
    );
  } catch (err) {
    console.warn(`Webhook signature verification failed: ${err.message}`);
    return res.sendStatus(400);
  }

  try {
    await handleEvent(event, ctx);
    res.sendStatus(200);
  } catch (err) {
    // Non-2xx makes Stripe retry with backoff.
    console.error(`Error handling ${event.type} (${event.id}):`, err);
    res.sendStatus(500);
  }
});

app.use(express.json());

// Content-Security-Policy for pages that redirect to Stripe-hosted Checkout.
app.use((req, res, next) => {
  res.setHeader(
    'Content-Security-Policy',
    "default-src 'self'; script-src 'self' https://*.stripe.com; frame-src https://*.stripe.com; connect-src 'self' https://*.stripe.com",
  );
  next();
});

app.use(express.static(path.resolve('public')));

// TODO: replace with your real authentication. The member's identity must come
// from your session/login, never from an untrusted request body in production.
function currentUser(req) {
  const { userId, email, name } = req.body ?? {};
  if (!userId || !email) {
    const err = new Error('userId and email are required');
    err.status = 400;
    throw err;
  }
  return { userId, email, name };
}

function requireAdmin(req, res, next) {
  const expected = process.env.ADMIN_API_TOKEN;
  const given = (req.headers.authorization ?? '').replace(/^Bearer /, '');
  const ok =
    expected &&
    given.length === expected.length &&
    crypto.timingSafeEqual(Buffer.from(given), Buffer.from(expected));
  if (!ok) return res.sendStatus(401);
  next();
}

app.post('/api/checkout', async (req, res, next) => {
  try {
    const user = currentUser(req);
    const { plan, interval = 'monthly' } = req.body;
    const session = await createMembershipCheckout(ctx, { ...user, plan, interval, baseUrl: BASE_URL });
    res.json({ url: session.url });
  } catch (err) {
    next(err);
  }
});

app.post('/api/portal', async (req, res, next) => {
  try {
    const { userId } = currentUser(req);
    const session = await createPortalSession(ctx, { userId, baseUrl: BASE_URL });
    res.json({ url: session.url });
  } catch (err) {
    next(err);
  }
});

app.get('/api/members/:userId', (req, res) => {
  const member = store.getMember(req.params.userId);
  if (!member) return res.sendStatus(404);
  const { hasAccess = false, plan = null, subscriptionStatus = null, currentPeriodEnd = null, paymentIssue = null } = member;
  res.json({ hasAccess, plan, subscriptionStatus, currentPeriodEnd, paymentIssue });
});

// Staff-only: raise and email an invoice for one-off services.
app.post('/api/admin/invoices', requireAdmin, async (req, res, next) => {
  try {
    const invoice = await createAndSendInvoice(ctx, req.body);
    res.status(201).json({ id: invoice.id, status: invoice.status, hostedInvoiceUrl: invoice.hosted_invoice_url });
  } catch (err) {
    next(err);
  }
});

// Never echo Stripe keys or environment in errors.
app.use((err, req, res, _next) => {
  const status = err.status ?? (err.type === 'StripeCardError' ? 402 : 500);
  if (status >= 500) console.error(err);
  res.status(status).json({ error: status >= 500 ? 'Internal error' : err.message });
});

app.listen(PORT, () => console.log(`KPI Fitness billing running on ${BASE_URL}`));
