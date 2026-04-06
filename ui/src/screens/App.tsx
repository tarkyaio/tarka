import React from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./Shell";
import { InboxScreen } from "./InboxScreen";
import { CaseScreen } from "./CaseScreen";
import { ExecDashboardScreen } from "./ExecDashboardScreen";
import { ENABLE_LEARNING_LOOP } from "../lib/api";
import { ExecLearningScreen } from "./ExecLearningScreen";

export function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route path="/" element={<Navigate to="/inbox" replace />} />
        <Route path="/inbox" element={<InboxScreen />} />
        <Route path="/cases/:caseId" element={<CaseScreen />} />
        <Route path="/exec" element={<ExecDashboardScreen />} />
        {ENABLE_LEARNING_LOOP && <Route path="/exec/learning" element={<ExecLearningScreen />} />}
        <Route path="*" element={<Navigate to="/inbox" replace />} />
      </Route>
    </Routes>
  );
}
