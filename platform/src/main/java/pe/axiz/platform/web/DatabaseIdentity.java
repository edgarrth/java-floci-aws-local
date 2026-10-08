package pe.axiz.platform.web;

import java.sql.Connection;
import java.sql.SQLException;
import java.util.Properties;
import org.postgresql.Driver;
import org.postgresql.jdbc.SslMode;

public record DatabaseIdentity(String username, String schema, String sslMode, Boolean backendTls) {
  static DatabaseIdentity inspect(Connection connection) throws SQLException {
    // Se consulta el modo del driver de la conexión abierta, sin publicar su URL ni credenciales.
    var properties = Driver.parseURL(connection.getMetaData().getURL(), new Properties());
    if (properties == null) {
      throw new SQLException("No se pudo obtener la configuración PostgreSQL de la conexión");
    }
    String sslMode = SslMode.of(properties).value;
    try (var statement = connection.createStatement();
        var result =
            statement.executeQuery(
                "SELECT current_user AS username,current_schema() AS schema,"
                    + "(SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()) AS backend_tls")) {
      if (!result.next()) {
        throw new SQLException("No se pudo consultar la identidad de la sesión PostgreSQL");
      }
      // pg_stat_ssl describe el tramo hacia el motor, después de la terminación TLS del proxy.
      return new DatabaseIdentity(
          result.getString("username"),
          result.getString("schema"),
          sslMode,
          result.getObject("backend_tls", Boolean.class));
    }
  }
}
