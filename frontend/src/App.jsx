import { Navigate, Route, Routes } from "react-router-dom";
import Diagnosis, { DiagnosisResultPage } from "./pages/Diagnosis.jsx";
import InitialSettings from "./pages/InitialSettings.jsx";
import Connect from "./pages/Connect.jsx";
import Home from "./pages/Home.jsx";
import Loading from "./pages/Loading.jsx";
import Comments from "./pages/Comments.jsx";

function App() {
  return (
    <Routes>
      <Route path="/" element={<Diagnosis />} />
      <Route path="/diagnosis-result" element={<DiagnosisResultPage />} />
      <Route path="/settings" element={<InitialSettings />} />
      <Route path="/connect" element={<Connect />} />
      <Route path="/home" element={<Home />} />
      <Route path="/videos/:videoId/loading" element={<Loading />} />
      <Route path="/videos/:videoId/comments" element={<Comments />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default App;
