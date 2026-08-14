export default function EmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="empty-state">
      <p>No projects yet.</p>
      <button type="button" className="gd-btn gd-btn-primary" onClick={onNew}>
        + New project
      </button>
    </div>
  );
}
