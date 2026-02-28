import React from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./Shell";
import { InboxScreen } from "./InboxScreen";
import { CaseScreen } from "./CaseScreen";

export function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route path="/" element={<Navigate to="/inbox" replace />} />
        <Route path="/inbox" element={<InboxScreen />} />
        <Route path="/cases/:caseId" element={<CaseScreen />} />
        <Route path="*" element={<Navigate to="/inbox" replace />} />
      </Route>
    </Routes>
  );
}
