# kotlinx.serialization: keep the generated serializers of the package model.
-keepattributes *Annotation*, InnerClasses
-keepclassmembers class app.tracking.viewer.data.** {
    *** Companion;
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class app.tracking.viewer.data.**$$serializer { *; }
