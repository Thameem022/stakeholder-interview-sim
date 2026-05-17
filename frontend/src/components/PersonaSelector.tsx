import React from 'react'
import { Users } from 'lucide-react'
import type { Persona } from '../api'

interface PersonaSelectorProps {
  personas: Persona[]
  selected: string | null
  onSelect: (key: string) => void
  disabled?: boolean
}

export const PersonaSelector: React.FC<PersonaSelectorProps> = ({
  personas,
  selected,
  onSelect,
  disabled = false,
}) => {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Users className="w-5 h-5 text-primary-500" />
        <h2 className="text-xl font-semibold">Select Persona</h2>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {personas.map((persona) => (
          <button
            key={persona.key}
            onClick={() => onSelect(persona.key)}
            disabled={disabled}
            className={`p-4 rounded-lg border-2 transition-all ${
              selected === persona.key
                ? 'border-primary-500 bg-primary-500/10 text-white'
                : 'border-dark-800 bg-dark-800 text-gray-300 hover:border-primary-500/50'
            } disabled:opacity-50 disabled:cursor-not-allowed`}
          >
            <p className="font-medium">{persona.display_name}</p>
          </button>
        ))}
      </div>
    </div>
  )
}
