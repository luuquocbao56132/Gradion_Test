export default function StylePanel({ styleText }: { styleText: string | null }) {
  if (!styleText) return null;
  return (
    <section className="side-note">
      <h5>Style</h5>
      <p>{styleText}</p>
    </section>
  );
}
