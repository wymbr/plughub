/**
 * VideoGrid
 *
 * 2-column layout for the video medium:
 *   ┌────────────────────────┬──────────┐
 *   │   Remote video (main)  │  Local   │
 *   │                        │  (PiP)   │
 *   └────────────────────────┴──────────┘
 *
 * Uses livekit-client VideoTrack elements via <video> refs — avoids the
 * @livekit/components-react dependency on React context, which requires a
 * LiveKitRoom provider wrapper we don't want to add to the whole page.
 */

import React, { useEffect, useRef } from "react";
import type { LocalTrack, RemoteTrack } from "livekit-client";
import { Track } from "livekit-client";

interface VideoGridProps {
  remoteTracks: Map<string, RemoteTrack[]>;
  localTracks:  LocalTrack[];
  /** Compact mode for supervisor view (smaller cells) */
  compact?: boolean;
}

function TrackVideo({
  track,
  className,
  muted = false,
}: {
  track: LocalTrack | RemoteTrack;
  className?: string;
  muted?: boolean;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (!videoRef.current) return;
    track.attach(videoRef.current);
    return () => { track.detach(videoRef.current!); };
  }, [track]);

  return (
    <video
      ref={videoRef}
      autoPlay
      playsInline
      muted={muted}
      className={className}
    />
  );
}

export const VideoGrid: React.FC<VideoGridProps> = ({
  remoteTracks,
  localTracks,
  compact = false,
}) => {
  // Flatten remote video tracks (take first participant's video)
  const remoteVideoTrack = (() => {
    for (const tracks of remoteTracks.values()) {
      const vid = tracks.find(t => t.kind === Track.Kind.Video);
      if (vid) return vid;
    }
    return null;
  })();

  const localVideoTrack = localTracks.find(t => t.kind === Track.Kind.Video) ?? null;

  const mainHeight = compact ? "h-32" : "h-64";
  const pipSize    = compact ? "w-20 h-16" : "w-28 h-20";

  return (
    <div className="relative flex bg-gray-950 rounded-lg overflow-hidden select-none">
      {/* Main remote video */}
      <div className={`flex-1 ${mainHeight} flex items-center justify-center bg-gray-900`}>
        {remoteVideoTrack ? (
          <TrackVideo
            track={remoteVideoTrack}
            className="w-full h-full object-cover"
          />
        ) : (
          <div className="flex flex-col items-center gap-2 text-gray-500">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="w-10 h-10"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M15 10l4.553-2.27A1 1 0 0121 8.645V15.36a1 1 0 01-1.447.894L15 14M3 8a2 2 0 012-2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8z"
              />
            </svg>
            <span className="text-xs text-gray-500">Aguardando vídeo…</span>
          </div>
        )}
      </div>

      {/* Local PiP — bottom-right corner */}
      {localVideoTrack && (
        <div
          className={`absolute bottom-2 right-2 ${pipSize} rounded-md overflow-hidden border-2 border-white/20 shadow-lg`}
        >
          <TrackVideo
            track={localVideoTrack}
            muted
            className="w-full h-full object-cover scale-x-[-1]" /* mirror local */
          />
        </div>
      )}
    </div>
  );
};
