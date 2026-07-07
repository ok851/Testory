# Service module ProGuard / R8 rules
# Keep accessibility service classes
-keep class com.testory.assistant.v2.service.accessibility.** { *; }
-keep class com.testory.assistant.v2.service.foreground.** { *; }

# Keep coroutine internals
-keepnames class kotlinx.coroutines.internal.MainDispatcherFactory {}
-keepnames class kotlinx.coroutines.CoroutineExceptionHandler {}
