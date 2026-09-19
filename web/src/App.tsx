import { NavLink, Route, Routes } from 'react-router-dom'
import { ConnectionBar } from './components/ConnectionBar'
import { CalibrationPage } from './pages/CalibrationPage'
import { FeedbackPage } from './pages/FeedbackPage'
import { TaskPage } from './pages/TaskPage'

export function App() {
  return (
    <>
      <header className="top">
        <h1>機器人控制台</h1>
        <ConnectionBar />
      </header>
      <nav className="tabs">
        <NavLink to="/" end>
          任務
        </NavLink>
        <NavLink to="/calibration">校正</NavLink>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<TaskPage />} />
          <Route path="/feedback/:runId" element={<FeedbackPage />} />
          <Route path="/calibration" element={<CalibrationPage />} />
          <Route path="*" element={<TaskPage />} />
        </Routes>
      </main>
    </>
  )
}
