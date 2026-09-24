import Stripe from 'stripe';

// Pin the API version the code was written against so a Dashboard upgrade
// can't change payload shapes underneath the webhook handler.
export const STRIPE_API_VERSION = '2026-08-26.dahlia';

let client = null;

// Lazily create a single client instance. Never use a global/module-level
// API key setter. Prefer a restricted key (rk_...) with only the permissions
// this service needs; see README.md.
export function getStripe() {
  if (!client) {
    const key = process.env.STRIPE_SECRET_KEY;
    if (!key) {
      throw new Error('STRIPE_SECRET_KEY is not set. See .env.example.');
    }
    client = new Stripe(key, {
      apiVersion: STRIPE_API_VERSION,
      appInfo: { name: 'kpi-fitness-billing' },
      maxNetworkRetries: 2,
    });
  }
  return client;
}
