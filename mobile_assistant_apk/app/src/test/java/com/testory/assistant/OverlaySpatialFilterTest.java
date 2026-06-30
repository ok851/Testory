package com.testory.assistant;

import org.junit.Test;

import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

/** Cover 空间过滤单元测试（对标 SoloPi checkInFloat）。 */
public class OverlaySpatialFilterTest {

    private static final int OL = 900;
    private static final int OT = 36;
    private static final int OR = 1080;
    private static final int OB = 96;
    private static final int SLOP = 6;

    @Test
    public void hitTestPointInsideOverlay() {
        assertTrue(OverlaySpatialFilter.intersectsOverlay(OL, OT, OR, OB, SLOP, 950, 60, 0, 0, 0, 0));
    }

    @Test
    public void hitTestPointOutsideOverlay() {
        assertFalse(OverlaySpatialFilter.intersectsOverlay(OL, OT, OR, OB, SLOP, 100, 200, 0, 0, 0, 0));
    }

    @Test
    public void hitTestBoundsIntersectOverlay() {
        assertTrue(OverlaySpatialFilter.intersectsOverlay(OL, OT, OR, OB, SLOP, 0, 0, 940, 50, 960, 70));
    }

    @Test
    public void hitTestBoundsMissOverlay() {
        assertFalse(OverlaySpatialFilter.intersectsOverlay(OL, OT, OR, OB, SLOP, 0, 0, 10, 10, 50, 50));
    }

    @Test
    public void emptyOverlayNeverIgnores() {
        assertFalse(OverlaySpatialFilter.intersectsOverlay(0, 0, 0, 0, SLOP, 950, 60, 0, 0, 0, 0));
    }

    @Test
    public void hitSlopExpandsOverlayHitRegion() {
        assertTrue(OverlaySpatialFilter.intersectsOverlay(OL, OT, OR, OB, SLOP, OL - 4, OT - 4, 0, 0, 0, 0));
    }
}
