import { useState } from 'preact/hooks';

export function WalletImportModal({ onClose, onImport }) {
  const [privateKey, setPrivateKey] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function submit(event) {
    event.preventDefault();
    if (privateKey.trim().length < 32) {
      setError('Please enter a valid Ethereum private key (hex format).');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      await onImport(privateKey.trim());
      onClose();
    } catch (nextError) {
      setError(nextError.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div class="modal-overlay" role="dialog" aria-modal="true" aria-label="Import Ethereum private key">
      <form class="modal-content" onSubmit={submit}>
        <div class="modal-header"><div class="modal-title">Import Ethereum Private Key</div><button class="modal-close" type="button" onClick={onClose}>✕</button></div>
        <div class="modal-body"><p style={{ fontSize: '13px', color: 'var(--text-dim)', marginBottom: '12px' }}>Paste an existing Ethereum private key. The server derives its public address and Lighter zk-key pair.</p><label class="form-group"><span class="form-label">Private Key</span><input class="form-input mono" type="password" value={privateKey} onInput={(event) => setPrivateKey(event.currentTarget.value)} placeholder="0x..." autoFocus autoComplete="off" /></label><div style={{ color: 'var(--red)', fontSize: '12px', marginTop: '8px' }}>{error}</div></div>
        <div class="modal-footer"><button class="btn btn-secondary" type="button" onClick={onClose}>Cancel</button><button class="btn btn-primary" disabled={submitting} type="submit">{submitting ? '⌛ Importing...' : 'Confirm Import'}</button></div>
      </form>
    </div>
  );
}
