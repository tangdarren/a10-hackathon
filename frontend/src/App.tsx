import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom';
import DispatchBoard from './DispatchBoard';
import OverviewPage from './OverviewPage';
import ReportPage from './ReportPage';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/orders/:orderId" element={<DispatchBoard />} />
        <Route path="/report" element={<ReportPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
