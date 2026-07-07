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

// ============================================================
// Brand Colors
// ============================================================
private val BrandBlue = Color(0xFF1565C0)
private val BrandBlueLight = Color(0xFF42A5F5)
private val BrandBlueDark = Color(0xFF0D47A1)
val BrandAccent = Color(0xFFFF6F00)
private val BrandSurface = Color(0xFFF8F9FA)

// Semantic status colors
val StatusSuccess = Color(0xFF2E7D32)
val StatusWarning = Color(0xFFF57F17)
val StatusError = Color(0xFFDC3545)
val StatusRecording = Color(0xFFE53935)
val StatusReplaying = Color(0xFF1565C0)
val StatusIdle = Color(0xFF757575)

// ============================================================
// Light Color Scheme
// ============================================================
private val LightColorScheme = lightColorScheme(
    primary = BrandBlue,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFD1E4FF),
    onPrimaryContainer = BrandBlueDark,
    inversePrimary = BrandBlueLight,
    secondary = BrandAccent,
    onSecondary = Color.White,
    secondaryContainer = Color(0xFFFFDCC2),
    onSecondaryContainer = Color(0xFF331200),
    tertiary = Color(0xFF00897B),
    onTertiary = Color.White,
    tertiaryContainer = Color(0xFFA7FFEB),
    onTertiaryContainer = Color(0xFF002019),
    error = StatusError,
    onError = Color.White,
    errorContainer = Color(0xFFF9DEDC),
    onErrorContainer = Color(0xFF410E0B),
    background = BrandSurface,
    onBackground = Color(0xFF1C1B1F),
    surface = Color.White,
    onSurface = Color(0xFF1C1B1F),
    surfaceVariant = Color(0xFFE7E0EC),
    onSurfaceVariant = Color(0xFF49454F),
    surfaceTint = BrandBlue,
    inverseSurface = Color(0xFF313033),
    inverseOnSurface = Color(0xFFF4EFF4),
    outline = Color(0xFF79747E),
    outlineVariant = Color(0xFFCAC4D0),
    scrim = Color.Black
)

// ============================================================
// Dark Color Scheme
// ============================================================
private val DarkColorScheme = darkColorScheme(
    primary = BrandBlueLight,
    onPrimary = Color(0xFF003258),
    primaryContainer = BrandBlueDark,
    onPrimaryContainer = Color(0xFFD1E4FF),
    inversePrimary = BrandBlue,
    secondary = Color(0xFFFFB74D),
    onSecondary = Color(0xFF472A00),
    secondaryContainer = Color(0xFF663F00),
    onSecondaryContainer = Color(0xFFFFDCC2),
    tertiary = Color(0xFF4DB6AC),
    onTertiary = Color(0xFF003733),
    tertiaryContainer = Color(0xFF005048),
    onTertiaryContainer = Color(0xFFA7FFEB),
    error = Color(0xFFF2B8B5),
    onError = Color(0xFF601410),
    errorContainer = Color(0xFF8C1D18),
    onErrorContainer = Color(0xFFF9DEDC),
    background = Color(0xFF1C1B1F),
    onBackground = Color(0xFFE6E1E5),
    surface = Color(0xFF1C1B1F),
    onSurface = Color(0xFFE6E1E5),
    surfaceVariant = Color(0xFF49454F),
    onSurfaceVariant = Color(0xFFCAC4D0),
    surfaceTint = BrandBlueLight,
    inverseSurface = Color(0xFFE6E1E5),
    inverseOnSurface = Color(0xFF313033),
    outline = Color(0xFF938F99),
    outlineVariant = Color(0xFF49454F),
    scrim = Color.Black
)

// ============================================================
// Typography (Material 3 customized)
// ============================================================
val TestoryTypography = Typography(
    displayLarge = Typography().displayLarge,
    displayMedium = Typography().displayMedium,
    displaySmall = Typography().displaySmall,
    headlineLarge = Typography().headlineLarge,
    headlineMedium = Typography().headlineMedium,
    headlineSmall = Typography().headlineSmall,
    titleLarge = Typography().titleLarge,
    titleMedium = Typography().titleMedium,
    titleSmall = Typography().titleSmall,
    bodyLarge = Typography().bodyLarge,
    bodyMedium = Typography().bodyMedium,
    bodySmall = Typography().bodySmall,
    labelLarge = Typography().labelLarge,
    labelMedium = Typography().labelMedium,
    labelSmall = Typography().labelSmall
)

// ============================================================
// Animation constants
// ============================================================
object AnimationDefaults {
    const val SHORT_DURATION = 200
    const val MEDIUM_DURATION = 400
    const val LONG_DURATION = 600

    val defaultTween = tween<Float>(MEDIUM_DURATION)
    val fastTween = tween<Float>(SHORT_DURATION)
    val slowTween = tween<Float>(LONG_DURATION)
    val defaultSlideTween = tween<IntOffset>(MEDIUM_DURATION)
}

// Standard navigation transition
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

// ============================================================
// Theme Composable
// ============================================================
@Composable
fun TestoryTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    dynamicColor: Boolean = true, // Material You on Android 12+
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
            window.statusBarColor = android.graphics.Color.TRANSPARENT
            window.navigationBarColor = android.graphics.Color.TRANSPARENT
            WindowCompat.getInsetsController(window, view).apply {
                isAppearanceLightStatusBars = !darkTheme
                isAppearanceLightNavigationBars = !darkTheme
            }
        }
    }

    MaterialTheme(
        colorScheme = colorScheme,
        typography = TestoryTypography,
        content = content
    )
}
