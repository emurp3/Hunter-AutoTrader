import { useRef, useState } from 'react'

const HERO_VIDEO_PATH = '/media/hunter-hero-loop.mp4'

export default function HeroPage({ onEnter }) {
  const videoRef = useRef(null)
  const [videoFailed, setVideoFailed] = useState(false)

  return (
    <div className="hero-root">
      {!videoFailed ? (
        <video
          ref={videoRef}
          className="hero-video hero-video--desktop"
          src={HERO_VIDEO_PATH}
          autoPlay
          loop
          muted
          playsInline
          onError={() => setVideoFailed(true)}
        />
      ) : (
        <div className="hero-video-fallback" />
      )}

      <div className="hero-overlay" />

      <div className="hero-content">
        <div className="hero-badge">v0.2.0</div>
        <h1 className="hero-title">HUNTER</h1>
        <p className="hero-tagline">Elite Liberation Agent</p>
        <p className="hero-sub">
          Autonomous Revenue Acquisition · Live Execution · Weekly Quota Enforcement
        </p>
        <button className="hero-cta" onClick={onEnter}>
          Enter Hunter Operations
        </button>
      </div>
    </div>
  )
}
