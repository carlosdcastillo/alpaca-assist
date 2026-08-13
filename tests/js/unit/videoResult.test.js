require("../../../web/js/utils/videoResult.js");

describe("VideoResultUtils", () => {
  const marker =
    "@@ALPACA_VIDEO_RESULT@@video/webm@@ALPACA_FIELD@@locator" +
    "@@ALPACA_FIELD@@6@@ALPACA_FIELD@@Recorded demo";

  beforeEach(() => {
    window.VideoResultUtils.clear();
    window.app = { currentTabId: "tab-1" };
    window.pythonAPI = {
      get_video_chunk: jest.fn(),
    };
    global.URL.createObjectURL = jest.fn().mockReturnValue("blob:demo");
    global.URL.revokeObjectURL = jest.fn();
  });

  it("parses metadata without carrying video bytes", () => {
    expect(window.VideoResultUtils.parse(marker)).toEqual({
      mimeType: "video/webm",
      locator: "locator",
      size: 6,
      description: "Recorded demo",
    });
    expect(marker).not.toContain("base64");
  });

  it("loads bounded chunks into one blob URL and caches it", async () => {
    window.pythonAPI.get_video_chunk
      .mockResolvedValueOnce({
        success: true,
        mime_type: "video/webm",
        size: 6,
        data: "YWJj",
        next_offset: 3,
        done: false,
      })
      .mockResolvedValueOnce({
        success: true,
        mime_type: "video/webm",
        size: 6,
        data: "ZGVm",
        next_offset: 6,
        done: true,
      });
    const parsed = window.VideoResultUtils.parse(marker);

    const first = await window.VideoResultUtils.load("tab-1", parsed);
    const second = await window.VideoResultUtils.load("tab-1", parsed);

    expect(first).toBe("blob:demo");
    expect(second).toBe(first);
    expect(window.pythonAPI.get_video_chunk).toHaveBeenCalledTimes(2);
    expect(global.URL.createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
  });

  it("revokes loaded blob URLs when the display is cleared", async () => {
    window.pythonAPI.get_video_chunk.mockResolvedValue({
      success: true,
      mime_type: "video/webm",
      size: 6,
      data: "YWJjZGVm",
      next_offset: 6,
      done: true,
    });
    await window.VideoResultUtils.load(
      "tab-1",
      window.VideoResultUtils.parse(marker),
    );

    window.VideoResultUtils.clear();

    expect(global.URL.revokeObjectURL).toHaveBeenCalledWith("blob:demo");
  });
});
