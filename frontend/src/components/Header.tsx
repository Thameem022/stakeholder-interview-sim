import React from 'react'
import { Podcast } from 'lucide-react'

export const Header: React.FC = () => {
  return (
    <header className="border-b border-dark-800 bg-gradient-to-r from-dark-800 to-dark-900">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="flex items-center gap-3">
          <Podcast className="w-10 h-10 text-primary-500" />
          <div>
            <h1 className="text-4xl font-bold text-white">RAG Interview Simulator</h1>
            <p className="text-gray-400 mt-1">Voice-powered conversations with AI personas</p>
          </div>
        </div>
      </div>
    </header>
  )
}
