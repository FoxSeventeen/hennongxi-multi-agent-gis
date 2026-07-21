export function MapWorkspace() {
  return (
    <section className="map-workspace" aria-labelledby="map-workspace-title">
      <div className="map-heading">
        <div>
          <p className="section-kicker">研究区域</p>
          <h2 id="map-workspace-title">神农溪地图工作区</h2>
        </div>
        <p className="map-state">
          <span aria-hidden="true" />
          等待任务
        </p>
      </div>

      <div className="map-canvas">
        <svg
          className="watershed-sketch"
          viewBox="0 0 960 640"
          role="img"
          aria-label="神农溪流域示意底图，任务完成后将在此显示真实分析图层"
        >
          <path
            className="contour contour-one"
            d="M61 179C165 78 318 112 391 50c96-82 233-24 291 48 67 82 158 92 209 184 62 112-2 239-92 293-111 66-202-17-312 9-124 29-246 14-325-66-88-89-171-242-101-339Z"
          />
          <path
            className="contour contour-two"
            d="M146 206c93-93 197-43 279-92 84-50 181-30 242 29 55 53 151 93 154 190 3 99-75 176-158 188-101 15-166-21-250 13-107 44-207-2-266-79-61-79-75-176-1-249Z"
          />
          <path
            className="contour contour-three"
            d="M248 221c73-69 160-54 230-83 80-33 152 13 196 63 48 54 84 121 49 192-33 67-112 73-181 74-80 2-150 51-218 4-69-48-139-172-76-250Z"
          />
          <path
            className="river-shadow"
            d="M731 106c-54 47-44 96-100 125-52 28-67 7-112 48-46 42-18 68-70 96-44 23-65-15-109 21-45 36-18 73-78 101-55 25-103 1-149 37"
          />
          <path
            className="river-line"
            d="M731 106c-54 47-44 96-100 125-52 28-67 7-112 48-46 42-18 68-70 96-44 23-65-15-109 21-45 36-18 73-78 101-55 25-103 1-149 37"
          />
          <circle className="monitor-point" cx="448" cy="375" r="8" />
          <circle className="monitor-ring" cx="448" cy="375" r="18" />
        </svg>

        <div className="map-location-label" aria-hidden="true">
          <span>31°19′N</span>
          <strong>神农溪流域</strong>
          <span>110°19′E</span>
        </div>
        <div className="map-empty-state">
          <p>地图已就位</p>
          <span>任务完成后，真实流域边界与 NDVI 图层将在这里叠加。</span>
        </div>
        <div className="map-scale" aria-hidden="true">
          <span />
          <small>10 km</small>
        </div>
      </div>
    </section>
  );
}
