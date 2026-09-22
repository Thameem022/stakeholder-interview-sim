/**
 * Presentation metadata for the persona cards.
 *
 * The API deliberately returns only {key, display_name}; the role tags and
 * blurbs are copy. The images reuse the interview assets that already ship in
 * public/ (same convention Avatar.tsx uses), so a card previews the actual
 * interview rather than an empty room:
 *
 *   image    the backdrop the 3D avatar is composited over  (/background/…)
 *   portrait a head-and-shoulders render of that same avatar (/personas/…)
 *
 * `portrait` is optional and layers on top of `image`. When the file is absent
 * the card falls back to the backdrop alone, so a persona without a render
 * still looks deliberate instead of broken.
 *
 * Unknown keys fall back to an empty meta so a newly added persona still
 * renders a card.
 */

export interface PersonaMeta {
  role: string
  description: string
  image?: string
  portrait?: string
}

const META: Record<string, PersonaMeta> = {
  alex_martinez: {
    role: 'Municipal planner',
    description:
      'Roleplays a municipal planner working the Harbortown floodplain question from inside the process.',
    image: '/background/alex_martinez_bg.jpg',
    portrait: '/personas/alex_martinez.png',
  },
  michael_mike_alvarez: {
    role: 'Waterfront resident',
    description:
      'Three decades on the working waterfront. Holds no formal role, but remembers every adjustment the town has made.',
    image: '/background/michael_mike_alvarez_bg.avif',
    portrait: '/personas/michael_mike_alvarez.png',
  },
  sarah_donnelly: {
    role: 'Small business owner',
    description:
      'Runs a downtown business in the most exposed, most visible part of town. Judges plans on credibility and feasibility.',
    image: '/background/sarah_donnelly_bg.jpg',
    portrait: '/personas/sarah_donnelly.png',
  },
  thomas_tom_caldwell: {
    role: 'Developer',
    description:
      'Indirect but significant power: capital, timing, and the ability to move projects when others cannot.',
    image: '/background/thomas_tom_caldwell_bg.jpg',
    portrait: '/personas/thomas_tom_caldwell.png',
  },
}

export const personaMeta = (key: string): PersonaMeta =>
  META[key] ?? { role: '', description: '' }
