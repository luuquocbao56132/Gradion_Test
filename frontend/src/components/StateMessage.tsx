type Props =
  | { kind: 'loading'; label: string }
  | { kind: 'error'; message: string; onRetry: () => void }
  | { kind: 'empty'; message: string; action?: React.ReactNode };

export default function StateMessage(props: Props) {
  if (props.kind === 'loading') {
    return (
      <div className="state-block" role="status" aria-live="polite">
        <span className="spinner" aria-hidden="true" />
        <span>{props.label}</span>
      </div>
    );
  }
  if (props.kind === 'error') {
    return (
      <div className="state-block error" role="alert">
        <p>{props.message}</p>
        <button type="button" className="gd-btn gd-btn-secondary" onClick={props.onRetry}>
          Try again
        </button>
      </div>
    );
  }
  return (
    <div className="empty-state">
      <p>{props.message}</p>
      {props.action}
    </div>
  );
}
