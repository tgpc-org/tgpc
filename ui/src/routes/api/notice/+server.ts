
export async function GET({ fetch }) {
  try {
    const resp = await fetch('/notice.json');
    if (resp.ok) {
      const data = await resp.json();
      return Response.json(data);
    }
  } catch {}

  return Response.json([]);
}
