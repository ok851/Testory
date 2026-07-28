package com.testory.assistant.v2.ui.theme

import android.app.Activity
import android.os.Build
import androidx.compose.animation.AnimatedContentTransitionScope
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.IntOffset
import androidx.core.view.WindowCompat

// Testory 品牌：石板青 + 琥珀点缀（对齐桌面壳，避免默认紫堆砌）
private val BrandPrimary = Color(0xFF0F766E)
private val BrandPrimaryLight = Color(0xFF2DD4BF)
private val BrandPrimaryDark = Color(0xFF115E59)
val BrandAccent = Color(0xFFEA580C)
private val BrandSurface = Color(0xFFF8FAFC)
private val BrandInk = Color(0xFF0F172A)

val StatusSuccess = Color(0xFF15803D)
val StatusWarning = Color(0xFFCA8A04)
val StatusError = Color(0xFFDC2626)
val StatusRecording = Color(0xFFDC2626)
val StatusReplaying = Color(0xFF0F766E)
val StatusIdle = Color(0xFF64748B)

private val LightColorScheme = lightColorScheme(
    primary = BrandPrimary,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFCCFBF1),
    onPrimaryContainer = BrandPrimaryDark,
    inversePrimary = BrandPrimaryLight,
    secondary = BrandAccent,
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFFFEDD5),
    onSecondaryContainer = Color(0xFF7C2D12),
    tertiary = Color(0xFF0369A1),
    onTertiary = Color.White,
    tertiaryContainer = Color(0xFFE0F2FE),
    onTertiaryContainer = Color(0xFF0C4A6E),
    error = StatusError,
    onError = Color.White,
    errorContainer = Color(0xFFFEE2E2),
    onErrorContainer = Color(0xFF7F1D1D),
    background = BrandSurface,
    onBackground = BrandInk,
    surface = Color.White,
    onSurface = BrandInk,
    surfaceVariant = Color(0xFFE2E8F0),
    onSurfaceVariant = Color(0xFF475569),
    surfaceTint = BrandPrimary,
    inverseSurface = Color(0xFF1E293B),
    inverseOnSurface = Color(0xFFF1F5F9),
    outline = Color(0xFF94A3B8),
    outlineVariant = Color(0xFFCBD5E1),
    scrim = Color.Black
)

private val DarkColorScheme = darkColorScheme(
    primary = BrandPrimaryLight,
    onPrimary = Color(0xFF134E4A),
    primaryContainer = BrandPrimaryDark,
    onPrimaryContainer = Color(0xFFCCFBF1),
    inversePrimary = BrandPrimary,
    secondary = Color(0xFFFB923C),
    onSecondary = Color(0xFF7C2D12),
    secondaryContainer = Color(0xFF9A3412),
    onSecondaryContainer = Color(0xFFFFEDD5),
    tertiary = Color(0xFF38BDF8),
    onTertiary = Color(0xFF0C4A6E),
    tertiaryContainer = Color(0xFF075985),
    onTertiaryContainer = Color(0xFFE0F2FE),
    error = Color(0xFFFCA5A5),
    onError = Color(0xFF7F1D1D),
    errorContainer = Color(0xFF991B1B),
    onErrorContainer = Color(0xFFFEE2E2),
    background = Color(0xFF0B1220),
    onBackground = Color(0xFFE2E8F0),
    surface = Color(0xFF0F172A),
    onSurface = Color(0xFFE2E8F0),
    surfaceVariant = Color(0xFF1E293B),
    onSurfaceVariant = Color(0xFFCBD5E1),
    surfaceTint = BrandPrimaryLight,
    inverseSurface = Color(0xFFE2E8F0),
    inverseOnSurface = Color(0xFF0F172A),
    outline = Color(0xFF64748B),
    outlineVariant = Color(0xFF334155),
    scrim = Color.Black
)

val TestoryTypography = Typography()

object AnimationDefaults {
    const val SHORT_DURATION = 200
    const val MEDIUM_DURATION = 400
    const val LONG_DURATION = 600

    val defaultTween = tween<Float>(MEDIUM_DURATION)
    val fastTween = tween<Float>(SHORT_DURATION)
    val slowTween = tween<Float>(LONG_DURATION)
    val defaultSlideTween = tween<IntOffset>(MEDIUM_DURATION)
}

fun AnimatedContentTransitionScope<*>.navTransition() =
    fadeIn(animationSpec = AnimationDefaults.defaultTween) +
        slideIntoContainer(
            towards = AnimatedContentTransitionScope.SlideDirection.Left,
            animationSpec = AnimationDefaults.defaultSlideTween
        )

fun AnimatedContentTransitionScope<*>.navPopTransition() =
    fadeOut(animationSpec = AnimationDefaults.defaultTween) +
        slideOutOfContainer(
            towards = AnimatedContentTransitionScope.SlideDirection.Right,
            animationSpec = AnimationDefaults.defaultSlideTween
        )

@Composable
fun TestoryTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    dynamicColor: Boolean = false,
    content: @Composable () -> Unit
) {
    val colorScheme = when {
        dynamicColor && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
            val context = LocalView.current.context
            if (darkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        }
        darkTheme -> DarkColorScheme
        else -> LightColorScheme
    }
    val view = LocalView.current
    if (!view.isInEditMode) {
        SideEffect {
            val window = (view.context as Activity).window
            window.statusBarColor = colorScheme.surface.toArgb()
            WindowCompat.getInsetsController(window, view).isAppearanceLightStatusBars = !darkTheme
        }
    }
    MaterialTheme(
        colorScheme = colorScheme,
        typography = TestoryTypography,
        content = content
    )
}
