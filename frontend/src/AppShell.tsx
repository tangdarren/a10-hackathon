import { QRCodeSVG } from 'qrcode.react';
import { useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { clearWorkOrders, fetchDemoLink, seedWorkOrders } from './api';
import './App.css';

type AppShellProps = {
  children: ReactNode;
  title?: string;
};

export default function AppShell({ children, title = 'Dispatch' }: AppShellProps) {
  const [showQr, setShowQr] = useState(false);
  const [reportUrl, setReportUrl] = useState(() =>
    new URL('/report', window.location.origin).toString(),
  );

  useEffect(() => {
    let cancelled = false;
    void fetchDemoLink()
      .then((link) => {
        if (!cancelled) setReportUrl(link.report_url);
      })
      .catch(() => {
        // Keep page-origin fallback.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const reset = async () => {
    try {
      await clearWorkOrders();
      await seedWorkOrders();
      window.location.assign('/');
    } catch {
      window.location.assign('/');
    }
  };

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">
          <span className="brand-mark" aria-hidden="true" />
          <h1>{title}</h1>
        </Link>
        <div className="topbar-actions">
          <button
            className="reset-btn"
            type="button"
            onClick={() => setShowQr(true)}
          >
            Resident QR
          </button>
          <button className="reset-btn" type="button" onClick={() => void reset()}>
            Reset
          </button>
        </div>
      </header>

      {children}

      {showQr && (
        <div
          className="qr-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Resident request QR code"
          onClick={() => setShowQr(false)}
        >
          <div
            className="qr-panel"
            onClick={(event) => event.stopPropagation()}
          >
            <p className="qr-eyebrow">Resident request</p>
            <h2>Scan to report a problem</h2>
            <p className="qr-copy">
              Opens a simple form like a tenant asking for help in their unit.
            </p>
            <div className="qr-code">
              <QRCodeSVG value={reportUrl} size={220} />
            </div>
            <p className="qr-url">{reportUrl}</p>
            <button
              className="reset-btn"
              type="button"
              onClick={() => setShowQr(false)}
            >
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
