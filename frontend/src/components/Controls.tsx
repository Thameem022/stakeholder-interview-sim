import React from 'react'
import { RotateCcw } from 'lucide-react'

interface ControlsProps {
  onReset: () => void
  isProcessing?: boolean
}

export const Controls: React.FC<ControlsProps> = ({ onReset, isProcessing = false }) => {
  return (
    <div className="flex gap-3">
      <button
        onClick={onReset}
        disabled={isProcessing}
        className="flex items-center gap-2 px-6 py-2 bg-dark-800 hover:bg-dark-700 text-gray-300 hover:text-white rounded-lg font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <RotateCcw className="w-4 h-4" />
        Reset
      </button>
    </div>
  )
}
