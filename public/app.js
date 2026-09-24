const $ = (id) => document.getElementById(id);

async function post(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error ?? 'Something went wrong');
  return data;
}

const me = () => ({ userId: $('userId').value, email: $('email').value });

async function go(url, body) {
  $('error').textContent = '';
  try {
    const { url: redirect } = await post(url, body);
    window.location.href = redirect;
  } catch (err) {
    $('error').textContent = err.message;
  }
}

document.querySelectorAll('[data-plan]').forEach((btn) =>
  btn.addEventListener('click', () =>
    go('/api/checkout', { ...me(), plan: btn.dataset.plan, interval: $('interval').value }),
  ),
);

$('portal').addEventListener('click', () => go('/api/portal', me()));
