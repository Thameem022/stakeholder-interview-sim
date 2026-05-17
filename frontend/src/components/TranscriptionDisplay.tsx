import React from 'react'
import { MessageCircle } from 'lucide-react'

interface TranscriptionDisplayProps {
  text: string
  confidence?: number
}

export const TranscriptionDisplay: React.FC<TranscriptionDisplayProps> = ({
  text,
  confidence,
}) => {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <MessageCircle className="w-5 h-5 text-primary-500" />
        <h3 className="font-semibold">Your Question</h3>
      </div>
      <div className="bg-dark-800 rounded-lg p-4 border border-dark-700">
        <p className="text-gray-100">{text}</p>
        {confidence && (
          <p className="text-xs text-gray-500 mt-2">
            Confidence: {(confidence * 100).toFixed(0)}%
          </p>
        )}
      </div>
    </div>
  )
}
