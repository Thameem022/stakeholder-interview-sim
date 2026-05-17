import { useLocation, useNavigate } from 'react-router-dom';
import ScoreReport, { type SessionEvaluation } from './ScoreReport';

export default function ScorePage() {
  const location = useLocation();
  const navigate = useNavigate();
  const state = location.state as { evaluation?: SessionEvaluation; conversation?: unknown[] } | null;
  const evaluation = state?.evaluation;
  const conversation = state?.conversation;

  const goBack = () => navigate('/', { state: { conversation, evaluation } });

  if (!evaluation) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-900 via-purple-900 to-slate-900 text-white flex items-center justify-center">
        <div className="text-center">
          <p className="text-slate-300 mb-4">No score report available. Complete an interview first.</p>
          <button
            onClick={goBack}
            className="px-6 py-3 rounded-full bg-slate-700 hover:bg-slate-600 transition-colors text-white font-medium"
          >
            ← Back to Interview
          </button>
        </div>
      </div>
    );
  }

  return (
    <ScoreReport
      evaluation={evaluation}
      onClose={goBack}
    />
  );
}
