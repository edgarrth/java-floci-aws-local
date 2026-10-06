package pe.axiz.platform.configuration;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import org.springframework.core.env.Environment;

public final class KafkaEndpoint {
  private KafkaEndpoint() {}

  public static String resolve(Environment environment) {
    String file = environment.getProperty("axiz.kafka-bootstrap-file", "");
    if (file.isBlank()) return environment.getRequiredProperty("axiz.kafka-bootstrap");
    try {
      return Files.readString(Path.of(file)).trim();
    } catch (IOException exception) {
      throw new IllegalStateException("No se pudo leer el endpoint MSK", exception);
    }
  }
}
